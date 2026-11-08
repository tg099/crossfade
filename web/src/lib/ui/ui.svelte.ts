// Interface state of Crossfade: navigation (tabs + stacks with real URLs), sheets, toasts,
// appearance and language. Data lives in lib/state.svelte.ts.
import { pushState, replaceState } from '$app/navigation';
import { put } from '#lib/api.ts';
import type { FadeInvite, Kind, Service, TrackInfo } from '#lib/types.ts';
import type { GlyphName } from './Glyph.svelte';
import { i18n, t, type Key, type LangPref } from './i18n.svelte.ts';
import { project, reducedMotion, rubberband, Spring } from './motion.ts';

export type Tab = 'home' | 'library' | 'fades' | 'search';
export type View = { kind: 'root' } | { kind: 'fade'; id: number };

export const TABS: { id: Tab; label: Key; icon: GlyphName; path: string }[] = [
	{ id: 'home', label: 'tab.home', icon: 'home', path: '/' },
	{ id: 'library', label: 'tab.library', icon: 'library', path: '/library' },
	{ id: 'fades', label: 'tab.fades', icon: 'fades', path: '/fades' },
	{ id: 'search', label: 'tab.search', icon: 'search', path: '/search' }
];

// ---------------------------------------------------------------- appearance
export type Theme = 'system' | 'light' | 'dark';
const BG = { light: '#fbfaf8', dark: '#0c0b0b' };
const CACHE = 'crossfade.prefs'; // last known theme/lang, so the first paint is right before the user is loaded

/** html[data-theme] forces a scheme; without it the CSS follows prefers-color-scheme */
export function applyTheme(th: Theme) {
	const root = document.documentElement;
	if (th === 'system') delete root.dataset.theme;
	else root.dataset.theme = th;
	for (const m of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
		m.content = th === 'system' ? (m.media.includes('dark') ? BG.dark : BG.light) : BG[th];
	}
}

// ---------------------------------------------------------------- shared shapes
export interface CtxItem {
	label: string;
	icon: GlyphName;
	role?: 'destructive';
	run: () => void;
}
export type CtxPreview = { kind: 'fade'; id: number } | { kind: 'track'; track: TrackInfo };
export interface Ctx {
	el: HTMLElement;
	rect: DOMRect;
	preview: CtxPreview;
	items: CtxItem[];
}
export type Template = 'likes' | 'copy' | null;

class UI {
	phone = $state(false);
	theme = $state<Theme>('system');
	/** open sheets bottom → top, with their presentation progress 0…1 */
	sheets = $state<{ id: number; p: number; h?: number }[]>([]);
	toast = $state<{ id: number; text: string; icon: GlyphName; tone: 'good' | 'info' | 'fault'; undo?: () => void } | null>(null);
	// sheets
	accountOpen = $state(false);
	/** a service to unfold in the account sheet (e.g. after the Spotify sign-in) */
	serviceFocus = $state<Service | null>(null);
	serviceOpen = $state(false);
	usersOpen = $state(false);
	/** null = closed, 0 = new user, else user id */
	userEditing = $state<number | null>(null);
	userOpen = $state(false);
	appsOpen = $state(false);
	notifyOpen = $state(false);
	healthOpen = $state(false);
	backupOpen = $state(false);
	historyOpen = $state(false);
	/** time travel opened right at one collection (from a fade); null = the list of all */
	historyTarget = $state<{ account_id: number; kind: Kind; collection_id: string } | null>(null);
	passwordOpen = $state(false);
	friendsOpen = $state(false);
	/** an invitation into a friend's fade, being answered */
	joining = $state<FadeInvite | null>(null);
	joinOpen = $state(false);
	/** null = closed, 0 = new fade, else fade id */
	editing = $state<number | null>(null);
	editorTemplate = $state<Template>(null);
	editorOpen = $state(false);
	planFade = $state<number | null>(null);
	planOpen = $state(false);
	reviewFade = $state<number | null>(null);
	reviewOpen = $state(false);
	/** song details within a fade (versions per collection) */
	song = $state<{ fadeId: number; songId: number } | null>(null);
	songOpen = $state(false);
	/** bumped when a version was swapped, so open song lists reload */
	songChanged = $state(0);
	ctx = $state<Ctx | null>(null);
	/** one app-wide confirmation for destructive actions */
	ask = $state<{ title: string; message?: string; label: string; run: () => void } | null>(null);
	// search
	query = $state('');
	/** phone: the dock turned into a search field */
	searching = $state(false);
	#toastTimer = 0;

	/** what we knew last time (theme, language), until the signed-in user arrives */
	loadCached() {
		try {
			const c = JSON.parse(localStorage.getItem(CACHE) ?? '{}');
			this.applyPrefs(c.theme ?? 'system', c.lang ?? 'system');
		} catch {
			this.applyPrefs('system', 'system');
		}
	}

	applyPrefs(theme: Theme, lang: LangPref) {
		this.theme = theme;
		applyTheme(theme);
		i18n.set(lang);
		try {
			localStorage.setItem(CACHE, JSON.stringify({ theme, lang }));
		} catch {
			/* only a cache */
		}
	}

	/** stored with the user's account, so it follows them to every device */
	setTheme(th: Theme) {
		if (th === this.theme) return;
		const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
		const apply = () => this.applyPrefs(th, i18n.pref);
		if (!reducedMotion() && doc.startViewTransition) doc.startViewTransition(apply);
		else apply();
		put('/api/auth/me', { theme: th }).catch(() => this.say(t('err.notSaved'), 'alert', 'fault'));
	}

	setLang(l: LangPref) {
		if (l === i18n.pref) return;
		this.applyPrefs(this.theme, l);
		put('/api/auth/me', { lang: l }).catch(() => this.say(t('err.notSaved'), 'alert', 'fault'));
	}

	/** feedback; with `undo` the toast offers "Undo" and stays a little longer */
	say(text: string, icon: GlyphName = 'check', tone: 'good' | 'info' | 'fault' = 'good', undo?: () => void) {
		clearTimeout(this.#toastTimer);
		this.toast = { id: Date.now(), text, icon, tone, undo };
		this.#toastTimer = window.setTimeout(() => (this.toast = null), undo ? 5000 : tone === 'fault' ? 7000 : 3200);
	}
	fail(e: unknown) {
		this.say((e as Error)?.message || t('err.generic'), 'alert', 'fault');
	}

	edit(id: number | 0, template: Template = null) {
		this.editing = id;
		this.editorTemplate = template;
		this.editorOpen = true;
	}
	review(id: number) {
		this.reviewFade = id;
		this.reviewOpen = true;
	}
	showPlan(id: number) {
		this.planFade = id;
		this.planOpen = true;
	}
	showSong(fadeId: number, songId: number) {
		this.song = { fadeId, songId };
		this.songOpen = true;
	}
	openAccount(focus: Service | null = null) {
		this.accountOpen = true;
		if (focus) this.openService(focus);
	}
	join(inv: FadeInvite) {
		this.joining = inv;
		this.joinOpen = true;
	}
	timeTravel(target: { account_id: number; kind: Kind; collection_id: string } | null = null) {
		this.historyTarget = target;
		this.historyOpen = true;
	}
	openService(s: Service) {
		this.serviceFocus = s;
		this.serviceOpen = true;
	}
}
export const ui = new UI();

// ---------------------------------------------------------------- navigation
// Each tab keeps its own stack (like iOS). Every step has a URL; pushes use SvelteKit's shallow
// routing, so the browser's back button, the swipe-back gesture and reloads all agree.

type NavState = { cf?: { tab: Tab; depth: number; fade?: number } };

export function fromPath(path: string): { tab: Tab; stack: View[] } {
	const m = path.match(/^\/fades\/(\d+)/);
	if (m) return { tab: 'fades', stack: [{ kind: 'root' }, { kind: 'fade', id: Number(m[1]) }] };
	const tab = TABS.find((x) => x.path !== '/' && path.startsWith(x.path))?.id ?? 'home';
	return { tab, stack: [{ kind: 'root' }] };
}

class Nav {
	tab = $state<Tab>('home');
	/** the tab to return to when search is left */
	lastTab = $state<Tab>('home');
	stacks = $state<Record<Tab, View[]>>({
		home: [{ kind: 'root' }],
		library: [{ kind: 'root' }],
		fades: [{ kind: 'root' }],
		search: [{ kind: 'root' }]
	});
	/** horizontal offset of the top view during push / pop (px) */
	x = $state(0);
	/** a transition is in flight or a finger is on it: the view below must be visible */
	moving = $state(false);
	#spring = new Spring(0, (v) => (this.x = v), 0.5);
	#panFrom = 0;

	get stack() {
		return this.stacks[this.tab];
	}
	get W() {
		return window.innerWidth;
	}
	get animated() {
		return ui.phone && !reducedMotion();
	}

	#url(tab = this.tab, stack = this.stack) {
		const top = stack[stack.length - 1];
		return top.kind === 'fade' ? `/fades/${top.id}` : TABS.find((x) => x.id === tab)!.path;
	}
	#state(): NavState {
		const top = this.stack[this.stack.length - 1];
		return { cf: { tab: this.tab, depth: this.stack.length - 1, fade: top.kind === 'fade' ? top.id : undefined } };
	}

	/** first load: the URL decides where we are */
	init(path: string) {
		const { tab, stack } = fromPath(path);
		this.tab = tab;
		if (tab !== 'search') this.lastTab = tab;
		this.stacks[tab] = stack;
		replaceState(this.#url(), this.#state());
	}

	select(tab: Tab) {
		if (tab !== 'search') {
			ui.searching = false;
			this.lastTab = tab;
		}
		if (tab === this.tab) return this.toRoot();
		this.#spring.set(0);
		this.moving = false;
		this.tab = tab;
		replaceState(this.#url(), this.#state());
	}

	push(view: View) {
		this.stack.push(view);
		pushState(this.#url(), this.#state());
		if (!this.animated) return;
		this.moving = true;
		this.#spring.set(this.W);
		this.#spring.to(0, { response: 0.42 }).then((done) => done && (this.moving = false));
	}

	/** Desktop: the sidebar selects a fade directly. Phone: push it in the current tab. */
	openFade(id: number) {
		if (ui.phone) return this.push({ kind: 'fade', id });
		this.tab = 'fades';
		this.lastTab = 'fades';
		this.stacks.fades = [{ kind: 'root' }, { kind: 'fade', id }];
		pushState(this.#url(), this.#state());
	}

	back() {
		if (this.stack.length > 1) history.back();
	}

	toRoot() {
		const n = this.stack.length - 1;
		if (n < 1) {
			document.querySelector<HTMLElement>(`[data-tab="${this.tab}"] .view.top .scroller`)?.scrollTo({ top: 0, behavior: reducedMotion() ? 'auto' : 'smooth' });
			return;
		}
		const finish = () => {
			this.stack.splice(1);
			this.#spring.set(0);
			this.moving = false;
			replaceState(this.#url(), this.#state());
		};
		if (!this.animated) return finish();
		this.stack.splice(1, n - 1);
		this.moving = true;
		this.#spring.to(this.W, { response: 0.36 }).then((d) => d && finish());
	}

	/** a fade was deleted: drop it from every stack */
	forget(id: number) {
		for (const tab of Object.keys(this.stacks) as Tab[]) {
			this.stacks[tab] = this.stacks[tab].filter((v) => !(v.kind === 'fade' && v.id === id));
		}
		if (this.stack.length && this.stack[this.stack.length - 1].kind === 'root') replaceState(this.#url(), this.#state());
	}

	/**
	 * The browser moved through history (back/forward button, swipe from the edge, history.back()
	 * after our own gesture). Bring tabs and stacks in line; idempotent, so our own steps are no-ops.
	 */
	sync(state: NavState, path: string) {
		const want = state.cf ?? { ...fromPath(path), depth: fromPath(path).stack.length - 1 };
		const tab = want.tab;
		const stack = this.stacks[tab];
		if (tab !== this.tab) {
			this.tab = tab;
			if (tab !== 'search') this.lastTab = tab;
		}
		const depth = stack.length - 1;
		if (want.depth === depth) return;
		if (want.depth > depth) {
			// forward again
			const fade = 'fade' in want ? want.fade : undefined;
			if (fade) stack.push({ kind: 'fade', id: fade });
			return;
		}
		if (!this.animated || tab !== this.tab) {
			stack.splice(want.depth + 1);
			return;
		}
		stack.splice(want.depth + 2);
		this.moving = true;
		this.#spring.to(this.W, { response: 0.36 }).then((d) => {
			if (!d) return;
			stack.splice(want.depth + 1);
			this.#spring.set(0);
			this.moving = false;
		});
	}

	// interactive pop: the page follows the finger, then commits by momentum
	panStart() {
		this.#spring.stop();
		this.moving = true;
		this.#panFrom = this.x;
	}
	panMove(dx: number) {
		const x = this.#panFrom + dx;
		this.#spring.set(x < 0 ? -rubberband(-x, this.W) : x);
	}
	panEnd(v: number) {
		const proj = this.x + project(v, 0.99); // snappier than scrolling: a slow nudge falls back
		const pop = Math.abs(v) > 300 ? v > 0 : proj > this.W / 2;
		if (pop) {
			this.#spring.to(this.W, { response: 0.34, velocity: v }).then((d) => {
				if (!d) return;
				this.stack.pop();
				this.#spring.set(0);
				this.moving = false;
				history.back(); // the URL follows; sync() finds everything already in place
			});
		} else {
			this.#spring.to(0, { response: 0.38, velocity: v }).then((d) => d && (this.moving = false));
		}
	}
}
export const nav = new Nav();

// ---------------------------------------------------------------- helpers

export const mmss = (s: number | null | undefined) => {
	if (!s && s !== 0) return '';
	const x = Math.max(0, Math.floor(s));
	return `${Math.floor(x / 60)}:${String(x % 60).padStart(2, '0')}`;
};

/** relative time from a unix timestamp in seconds */
export function ago(ts: number | null | undefined) {
	if (!ts) return t('ago.never');
	const m = Math.round((Date.now() / 1000 - ts) / 60);
	if (m < 1) return t('ago.now');
	if (m < 60) return t('ago.min', { n: m });
	const h = Math.round(m / 60);
	if (h < 24) return t('ago.h', { n: h });
	const d = Math.round(h / 24);
	return d === 1 ? t('ago.yesterday') : t('ago.d', { n: d });
}

export function clock(ts: number | null | undefined) {
	if (!ts) return '–';
	const d = new Date(ts * 1000);
	const same = d.toDateString() === new Date().toDateString();
	return same
		? d.toLocaleTimeString(i18n.locale, { hour: '2-digit', minute: '2-digit' })
		: d.toLocaleString(i18n.locale, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
}

const INTERVALS: [number, Key][] = [
	[0, 'every.manual'],
	[15, 'every.15'],
	[30, 'every.30'],
	[60, 'every.60'],
	[180, 'every.180'],
	[720, 'every.720'],
	[1440, 'every.1440']
];
export const intervals = () => INTERVALS.map(([value, k]) => ({ value, label: t(k) }));
export const intervalLabel = (m: number) => {
	const k = INTERVALS.find(([v]) => v === m)?.[1];
	return k ? t(k) : t('every.n', { n: m });
};

/** direction between two neighbouring collections, as a glyph */
export function flowIcon(a: { role: string }, b: { role: string }): 'right' | 'left' | 'both' {
	const fwd = a.role !== 'receive' && b.role !== 'send';
	const back = b.role !== 'receive' && a.role !== 'send';
	return fwd && !back ? 'right' : back && !fwd ? 'left' : 'both';
}

/** brand colours (logo + background of every page that isn't about one fade) */
export const BRAND = ['#ff3b4e', '#2e6bff'];

/** soft colour fields behind a page, from a few colours */
export function ambient(colors: string[]): string {
	// spread over the whole window (it stays put while the page scrolls)
	const spots = [
		['16%', '6%', '62%'],
		['86%', '10%', '56%'],
		['62%', '62%', '64%'],
		['10%', '92%', '58%']
	];
	// fewer colours than spots: repeat the given ones instead of mixing in unrelated hues –
	// three or four clashing colours at once is what makes a page look like a rainbow
	const base = colors.length ? colors : BRAND;
	return (
		spots
			.map((_, i) => base[i % base.length])
			.map((c, i) => `radial-gradient(${spots[i][2]} 46% at ${spots[i][0]} ${spots[i][1]}, color-mix(in srgb, ${c} var(--ambient, 22%), transparent), transparent 70%)`)
			.join(', ') || 'none'
	);
}

export async function copyText(text: string) {
	try {
		await navigator.clipboard.writeText(text);
		ui.say(t('common.copied'), 'copy');
	} catch {
		ui.say(t('common.copyFailed'), 'alert', 'info');
	}
}
