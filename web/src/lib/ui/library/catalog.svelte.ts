// Your music across all services ("Mediathek"): one entry per song, with the collections it is in.
// Shared by the library view, the search tab and the library sheets.
import { SvelteSet } from 'svelte/reactivity';
import { api, post } from '#lib/api.ts';
import { canPlay } from '#lib/player.svelte.ts';
import { indexEntry, search } from '#lib/search.ts';
import { app } from '#lib/state.svelte.ts';
import type { Catalog, CatalogSong, CatalogSource, Cleanup, Service, TrackInfo } from '#lib/types.ts';
import { i18n } from '../i18n.svelte.ts';
import { ui } from '../ui.svelte.ts';

export type Avail = 'any' | 'has' | 'missing';
export type Facet = 'artist' | 'album' | 'source' | 'line';
export type Special = 'videos' | 'explicit' | 'noline' | 'single' | 'versions';
export type Sort = 'relevance' | 'artist' | 'title' | 'album' | 'added' | 'duration';
export const SERVICES: Service[] = ['ytm', 'apple', 'spotify'];

const SPLIT = /\s*(?:,|\s&\s|\bfeat\.?|\bft\.)\s*/i;
export const artistsOf = (a: string) =>
	a
		.split(SPLIT)
		.map((x) => x.trim())
		.filter(Boolean);
export const kindRank = (s: CatalogSource) => ({ liked: 0, library: 1, playlist: 2 })[s.kind];

class CatalogState {
	data = $state<Catalog | null>(null);
	error = $state('');
	loading = $state(false);
	#lastFinished = 0;

	// filters of the library view
	query = $state('');
	avail = $state<Record<Service, Avail>>({ ytm: 'any', apple: 'any', spotify: 'any' });
	picked = $state<Record<Facet, string[]>>({ artist: [], album: [], source: [], line: [] });
	special = $state<Special[]>([]);
	sort = $state<Sort>('artist');

	selecting = $state(false);
	selected = new SvelteSet<number>();

	// sheets
	filterOpen = $state(false);
	/** which list the picker sheet shows */
	facet = $state<Facet>('artist');
	sourcesOpen = $state(false);
	songOpen = $state(false);
	song = $state<CatalogSong | null>(null);
	addOpen = $state(false);
	removeOpen = $state(false);
	cleanupOpen = $state(false);
	/** suggestions to tidy up (loaded with the library, refreshed after every catalog job) */
	cleanup = $state<Cleanup | null>(null);
	/** the songs an add or remove works on, plus a preselected service */
	target = $state<number[]>([]);
	targetService = $state<Service | null>(null);

	collator = $derived(new Intl.Collator(i18n.locale, { sensitivity: 'base', numeric: true }));
	sourceById = $derived(new Map((this.data?.sources ?? []).map((s) => [s.id, s])));
	lineById = $derived(new Map((this.data?.lines ?? []).map((l) => [l.id, l])));
	services = $derived(SERVICES.filter((svc) => this.data?.sources.some((s) => s.service === svc && s.included)));
	linesOfSong = $derived.by(() => {
		const m = new Map<number, number[]>();
		for (const l of this.data?.lines ?? []) for (const id of l.songs) (m.get(id) ?? m.set(id, []).get(id)!).push(l.id);
		return m;
	});
	/** folded title/artist/album per song, built once per load rather than per keystroke */
	index = $derived(new Map((this.data?.songs ?? []).map((s) => [s.id, indexEntry(s.t, s.a, s.al)])));
	songById = $derived(new Map((this.data?.songs ?? []).map((s) => [s.id, s])));
	unscanned = $derived((this.data?.sources ?? []).filter((s) => s.included && !s.scanned_at).length);

	async load(force = false) {
		if (this.loading || (this.data && !force)) return;
		this.loading = true;
		try {
			const d = await api<Catalog>('/api/catalog');
			this.data = d;
			app.catalogJob = d.job;
			this.error = '';
			this.loadCleanup();
			const ids = new Set(d.songs.map((s) => s.id));
			for (const id of [...this.selected]) if (!ids.has(id)) this.selected.delete(id);
		} catch (e) {
			this.error = (e as Error).message;
		} finally {
			this.loading = false;
		}
	}
	async loadCleanup() {
		try {
			this.cleanup = await api<Cleanup>('/api/catalog/cleanup');
		} catch {
			this.cleanup = null;
		}
	}
	/** how many suggestions there are (copies count per entry, duplicates per group) */
	cleanupCount = $derived.by(() => {
		const k = this.cleanup;
		if (!k) return 0;
		return k.duplicates.length + k.copies.length + k.unusual.length + k.playlists.length + (k.youtube?.candidates.length ? 1 : 0);
	});
	async dismiss(key: string) {
		if (!this.cleanup) return;
		const k = this.cleanup;
		k.duplicates = k.duplicates.filter((g) => g.key !== key);
		k.unusual = k.unusual.filter((u) => u.key !== key);
		k.playlists = k.playlists.filter((p) => p.key !== key);
		k.dismissed += 1;
		await post('/api/catalog/cleanup/dismiss', { key }).catch(ui.fail);
	}
	/** reload after a catalog job finished (called from an effect in the views) */
	sync() {
		const f = app.catalogFinished;
		if (f && f.at !== this.#lastFinished) {
			this.#lastFinished = f.at;
			this.load(true);
		}
	}

	presence(s: CatalogSong): Map<Service, number> {
		const m = new Map<Service, number>();
		for (const [src] of s.in) {
			const svc = this.sourceById.get(src)?.service;
			if (svc) m.set(svc, (m.get(svc) ?? 0) + 1);
		}
		return m;
	}
	refsOn(s: CatalogSong, svc: Service): Set<string> {
		return new Set(s.in.filter(([src]) => this.sourceById.get(src)?.service === svc).map(([, r]) => r));
	}
	/** something you can listen into: the first copy of the song on any service */
	track(s: CatalogSong): TrackInfo {
		const svcOf = (src: number) => this.sourceById.get(src)?.service ?? '';
		const [src, ref] = s.in.find(([src, ref]) => canPlay({ service: svcOf(src), id: ref })) ?? s.in[0] ?? [0, ''];
		const service = svcOf(src);
		return { service, id: ref, title: s.t, artist: s.a, album: s.al, duration: s.d, isrc: null, url: null, artwork: s.c, explicit: s.x };
	}

	hits = $derived(search(this.data?.songs ?? [], this.index, this.query));

	matches(s: CatalogSong, skip?: Facet): boolean {
		const hits = this.hits;
		if (hits && !hits.scores.has(s.id)) return false;
		const pres = this.presence(s);
		for (const svc of this.services) {
			if (this.avail[svc] === 'has' && !pres.has(svc)) return false;
			if (this.avail[svc] === 'missing' && pres.has(svc)) return false;
		}
		const p = this.picked;
		if (skip !== 'artist' && p.artist.length) {
			const mine = artistsOf(s.a);
			if (!p.artist.some((a) => mine.includes(a))) return false;
		}
		if (skip !== 'album' && p.album.length && !p.album.includes(s.al)) return false;
		if (skip !== 'source' && p.source.length && !s.in.some(([src]) => p.source.includes(String(src)))) return false;
		if (skip !== 'line' && p.line.length) {
			const ls = this.linesOfSong.get(s.id) ?? [];
			if (!p.line.some((l) => ls.includes(Number(l)))) return false;
		}
		for (const sp of this.special) {
			if (sp === 'videos' && !s.v) return false;
			if (sp === 'explicit' && !s.x) return false;
			if (sp === 'noline' && this.linesOfSong.has(s.id)) return false;
			if (sp === 'single' && pres.size !== 1) return false;
			if (sp === 'versions' && !this.services.some((svc) => this.refsOn(s, svc).size > 1)) return false;
		}
		return true;
	}

	filtered = $derived.by(() => {
		const out = (this.data?.songs ?? []).filter((s) => this.matches(s));
		const by = this.hits && this.sort === 'artist' ? 'relevance' : this.sort;
		const sc = this.hits?.scores;
		const c = this.collator;
		out.sort((a, b) => {
			if (by === 'relevance' && sc) {
				const d = (sc.get(b.id) ?? 0) - (sc.get(a.id) ?? 0);
				if (d) return d;
			}
			if (by === 'added') return b.f - a.f;
			if (by === 'duration') return (b.d ?? 0) - (a.d ?? 0);
			if (by === 'title') return c.compare(a.t, b.t) || c.compare(a.a, b.a);
			if (by === 'album') return c.compare(a.al || '￿', b.al || '￿') || c.compare(a.t, b.t);
			return c.compare(a.a, b.a) || c.compare(a.al, b.al) || c.compare(a.t, b.t);
		});
		return out;
	});

	filterCount = $derived(
		Object.values(this.avail).filter((a) => a !== 'any').length + Object.values(this.picked).flat().length + this.special.length
	);

	togglePick(f: Facet, key: string) {
		const list = this.picked[f];
		this.picked[f] = list.includes(key) ? list.filter((k) => k !== key) : [...list, key];
	}
	toggleSpecial(sp: Special) {
		this.special = this.special.includes(sp) ? this.special.filter((x) => x !== sp) : [...this.special, sp];
	}
	resetFilters() {
		this.avail = { ytm: 'any', apple: 'any', spotify: 'any' };
		this.picked = { artist: [], album: [], source: [], line: [] };
		this.special = [];
	}

	// ------------------------------------------------------------ actions
	async start(path: string, body: unknown = {}) {
		try {
			await post(path, body);
			return true;
		} catch (e) {
			ui.fail(e);
			return false;
		}
	}
	scan = () => this.start('/api/catalog/scan');
	stop = () => post('/api/catalog/stop').catch(ui.fail);

	openSong(s: CatalogSong) {
		this.song = s;
		this.songOpen = true;
	}
	openAdd(ids: number[], svc: Service | null = null) {
		this.target = ids;
		this.targetService = svc;
		this.addOpen = true;
	}
	openRemove(ids: number[], svc: Service | null = null) {
		this.target = ids;
		this.targetService = svc;
		this.removeOpen = true;
	}

	exportCsv(songs: CatalogSong[], head: string[], yes: string) {
		const esc = (v: string) => (/[;"\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
		const svcs = this.services;
		const rows = songs.map((s) => {
			const pres = this.presence(s);
			const cols = [...new Set(s.in.map(([src]) => src))].map((id) => this.sourceById.get(id)?.name ?? '');
			const mm = s.d ? `${Math.floor(s.d / 60)}:${String(s.d % 60).padStart(2, '0')}` : '';
			return [s.t, s.a, s.al, mm, ...svcs.map((svc) => (pres.has(svc) ? yes : '')), cols.join(', '), (this.linesOfSong.get(s.id) ?? []).map((l) => this.lineById.get(l)?.name).join(', ')];
		});
		const csv = '﻿' + [head, ...rows].map((r) => r.map((v) => esc(String(v ?? ''))).join(';')).join('\r\n');
		const a = document.createElement('a');
		a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
		a.download = `crossfade-${new Date().toISOString().slice(0, 10)}.csv`;
		a.click();
		setTimeout(() => URL.revokeObjectURL(a.href), 1000);
	}
}

export const catalog = new CatalogState();
