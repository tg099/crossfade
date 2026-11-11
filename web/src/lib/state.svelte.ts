// Data of the signed-in user: fades, services, catalog jobs, live events from the server.
import { api, put, session } from './api.ts';
import type { Activity, AssistantWaiting, Board, CatalogFinished, CatalogJob, Friends, Job, Line, Member, ServiceInfo, User } from './types.ts';
import { num, t, type Key } from './ui/i18n.svelte.ts';
import { ui } from './ui/ui.svelte.ts';
import { updates } from './updates.svelte.ts';

const FINISH: Record<string, Key> = {
	ok: 'run.ok',
	preview: 'run.preview',
	partial: 'run.partial',
	needs_confirm: 'run.needs_confirm',
	blocked: 'run.blocked',
	auth_error: 'run.auth_error',
	error: 'run.error',
	stopped: 'run.stopped'
};

export const memberKey = (m: Pick<Member, 'account_id' | 'kind' | 'collection_id'>) =>
	`${m.account_id}:${m.kind}:${m.collection_id}`;

class AppState {
	ready = $state(false);
	setupNeeded = $state(false);
	user = $state<User | null>(null);
	lines = $state<Line[]>([]);
	services = $state<ServiceInfo[]>([]);
	linesLoaded = $state(false);
	activity = $state<Activity | null>(null);
	board = $state<Board | null>(null);
	/** bumped whenever a run finishes, so open views can reload */
	finished = $state<{ line_id: number; status: string; at: number } | null>(null);
	/** catalog job of this user (scan / bulk remove / bulk add) and its last result */
	catalogJob = $state<CatalogJob | null>(null);
	catalogFinished = $state<CatalogFinished | null>(null);
	/** friends, requests and invitations into their fades */
	friends = $state<Friends>({ friends: [], incoming: [], outgoing: [], invites: [] });
	// what waits for you: friend requests, and invites you haven't answered (a sent join request waits for the owner)
	socialBadge = $derived(this.friends.incoming.length + this.friends.invites.filter((i) => !i.request).length);
	/** deletions an AI assistant proposed that only you can let happen */
	assistantWaiting = $state<AssistantWaiting[]>([]);
	/** bumped whenever an assistant's proposal changes, so an open assistant sheet reloads */
	assistantTick = $state(0);
	/** the session ended while the app was open: the sign-in page says so */
	expired = $state(false);

	#events: EventSource | null = null;

	/** which fades each collection appears in (a collection may be part of several) */
	interchanges = $derived.by(() => {
		const map = new Map<string, { id: number; color: string; icon: string; name: string }[]>();
		for (const l of this.lines) {
			for (const m of l.members) {
				const k = memberKey(m);
				if (!map.has(k)) map.set(k, []);
				map.get(k)!.push({ id: l.id, color: l.color, icon: l.icon, name: l.name });
			}
		}
		return map;
	});

	connected = $derived(this.services.filter((s) => s.connected));
	trouble = $derived(this.services.some((s) => s.connected && s.status === 'error'));

	constructor() {
		session.lost = () => {
			if (!this.user) return;
			ui.closeAll();
			this.signedOut();
			this.expired = true;
		};
	}

	async boot() {
		try {
			const s = await api<{ setup_needed: boolean; user: User | null }>('/api/auth/state');
			this.setupNeeded = s.setup_needed;
			if (s.user) await this.signedIn(s.user);
		} finally {
			this.ready = true;
		}
	}

	async signedIn(user: User) {
		this.user = user;
		ui.applyPrefs(user.theme, user.lang);
		if (user.must_change_password) return; // nothing else until the start password is replaced
		this.expired = false;
		await Promise.all([this.loadLines(), this.loadServices(), this.loadActivity(), this.loadFriends(), this.loadBoard(), this.loadAssistant()]);
		if (user.is_admin) updates.load();
		this.connectEvents();
	}

	signedOut() {
		this.user = null;
		this.lines = [];
		this.services = [];
		this.activity = null;
		this.board = null;
		this.friends = { friends: [], incoming: [], outgoing: [], invites: [] };
		this.assistantWaiting = [];
		this.catalogJob = null;
		this.linesLoaded = false;
		this.#events?.close();
		this.#events = null;
	}

	async loadLines() {
		this.lines = await api<Line[]>('/api/lines');
		this.linesLoaded = true;
	}

	async loadServices() {
		this.services = await api<ServiceInfo[]>('/api/services');
	}

	async loadActivity() {
		try {
			this.activity = await api<Activity>(`/api/lines/activity?limit=16&tz_offset=${new Date().getTimezoneOffset()}`);
		} catch {
			// not essential
		}
	}

	async loadBoard() {
		try {
			this.board = await api<Board>('/api/friends/board');
		} catch {
			// not essential
		}
	}

	async loadAssistant() {
		try {
			this.assistantWaiting = (await api<{ actions: AssistantWaiting[] }>('/api/mcp/pending')).actions;
		} catch {
			// not essential
		}
	}

	async loadFriends() {
		try {
			this.friends = await api<Friends>('/api/friends');
		} catch {
			// not essential
		}
	}

	async reloadLine(id: number) {
		try {
			const fresh = await api<Line>(`/api/lines/${id}`);
			const i = this.lines.findIndex((l) => l.id === id);
			if (i >= 0) this.lines[i] = fresh;
			else this.lines.push(fresh);
		} catch {
			this.lines = this.lines.filter((l) => l.id !== id);
		}
	}

	line(id: number): Line | undefined {
		return this.lines.find((l) => l.id === id);
	}

	/** your own order: the fade you care about most comes first everywhere */
	async moveLine(from: number, to: number) {
		// friends' fades follow your own ones and keep their place
		const own = this.lines.filter((l) => l.mine).length;
		if (!this.lines[from]?.mine) return;
		to = Math.min(to, own - 1);
		if (from === to) return;
		const before = this.lines;
		const arr = [...this.lines];
		const [l] = arr.splice(from, 1);
		arr.splice(to, 0, l);
		this.lines = arr;
		try {
			this.lines = await put<Line[]>('/api/lines', { ids: arr.map((x) => x.id) });
		} catch (e) {
			this.lines = before;
			ui.fail(e);
		}
	}

	async setPaused(id: number, paused: boolean) {
		const l = this.line(id);
		if (!l) return;
		l.paused = paused;
		try {
			const fresh = await put<Line>(`/api/lines/${id}/paused`, { paused });
			const i = this.lines.findIndex((x) => x.id === id);
			if (i >= 0) this.lines[i] = fresh;
			ui.say(t(paused ? 'fade.paused' : 'fade.resumed', { name: l.name }), paused ? 'pause' : 'play');
		} catch (e) {
			l.paused = !paused;
			ui.fail(e);
		}
	}

	connectEvents() {
		if (this.#events || typeof EventSource === 'undefined') return;
		const es = new EventSource('/api/events');
		// after a server restart (e.g. a nightly update) or a dropped connection, events were missed:
		// refresh everything they would have told us
		let opened = false;
		es.onopen = () => {
			if (opened) {
				this.loadLines();
				this.loadServices().catch(() => {});
				this.loadFriends();
				this.loadActivity();
				this.loadAssistant();
				this.assistantTick++;
			}
			opened = true;
		};
		es.onmessage = (msg) => {
			let ev: any;
			try {
				ev = JSON.parse(msg.data);
			} catch {
				return;
			}
			if (ev.type === 'catalog') return this.#onCatalog(ev);
			// an assistant proposed, ran or finished something
			if (ev.type === 'mcp') {
				this.assistantTick++;
				return void this.loadAssistant();
			}
			// an admin restored a backup: everything shown here may be gone
			if (ev.type === 'restored') return location.reload();
			// a friend asked, answered, joined or left: refresh what we show of them
			if (ev.type === 'social') {
				this.loadFriends();
				this.loadBoard();
				if (ev.what === 'fades') this.loadLines();
				return;
			}
			if (ev.type !== 'job') return;
			const line = this.line(ev.line_id);
			if (line) line.job = ev.job as Job | null;
			if (ev.finished) {
				const st = ev.finished.status as string;
				const tone = ['ok', 'preview'].includes(st) ? 'good' : st === 'stopped' ? 'info' : 'fault';
				const text = t(FINISH[st] ?? 'run.error', { name: line?.name ?? t('fade.one') });
				ui.say(text, tone === 'good' ? 'check' : tone === 'info' ? 'pause' : 'alert', tone);
				this.finished = { line_id: ev.line_id, status: st, at: Date.now() };
				// a preview you started opens right away; held deletions wait in the overview
				if (st === 'preview') ui.showPlan(ev.line_id);
				this.reloadLine(ev.line_id);
				if (st === 'ok' || st === 'partial') {
					this.loadActivity();
					this.loadBoard();
				}
				if (st === 'auth_error') this.loadServices();
			}
		};
		es.onerror = () => {
			// EventSource reconnects by itself unless the server refused it (e.g. the session ended):
			// then ask again in a moment – a 401 from that check signs out cleanly
			if (es.readyState === EventSource.CLOSED) {
				this.#events = null;
				setTimeout(() => {
					if (!this.user) return;
					this.loadServices()
						.then(() => this.user && this.connectEvents())
						.catch(() => setTimeout(() => this.user && this.connectEvents(), 15000));
				}, 5000);
			}
		};
		this.#events = es;
	}

	#onCatalog(ev: any) {
		this.catalogJob = ev.job as CatalogJob | null;
		const f = ev.finished;
		if (!f) return;
		this.catalogFinished = { ...f, at: Date.now() };
		const r = f.result ?? {};
		let text: string;
		if (f.status === 'stopped') text = t('lib.job.stopped');
		else if (f.status === 'error' || f.status === 'auth_error') text = f.message ?? t('err.generic');
		else if (f.action === 'scan') text = t('lib.job.scanned', { n: num(r.songs) });
		else if (f.action === 'remove') text = t('lib.job.removed', { n: num(r.removed), failed: r.failed ?? 0 });
		else if (f.action === 'dedupe') text = t('clean.done.dedupe', { n: num(r.removed), failed: r.failed ?? 0 });
		else if (f.action === 'swap') text = t('clean.done.swap', { n: num(r.swapped), without: r.without ?? 0 });
		else if (f.action === 'playlists') text = t('clean.done.playlists', { n: num(r.deleted), failed: r.failed ?? 0 });
		else if (f.action === 'undo') text = t('clean.done.undo', { n: num(r.restored) });
		else if (f.action === 'restore') text = t('tt.done', { n: num(r.added ?? 0), removed: r.removed ?? 0, failed: r.failed ?? 0 });
		else text = t('lib.job.added', { n: num(r.added), target: r.target ?? '', missing: r.missing?.length ?? 0 });
		const tone = f.status === 'ok' ? 'good' : f.status === 'stopped' || f.status === 'partial' ? 'info' : 'fault';
		ui.say(text, tone === 'fault' ? 'alert' : 'check', tone);
		if (f.status === 'auth_error' || f.status === 'partial') this.loadServices();
		if (f.action === 'restore' && (f.status === 'ok' || f.status === 'partial')) this.loadLines();
	}

	/** kept for older call sites: one way to give feedback */
	toast(text: string, tone: 'info' | 'good' | 'fault' = 'info') {
		ui.say(text, tone === 'fault' ? 'alert' : 'check', tone);
	}
}

export const app = new AppState();
