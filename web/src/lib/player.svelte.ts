import { api } from './api.ts';
import type { Service } from './types.ts';
import { t as tr } from './ui/i18n.svelte.ts';

/** What the player needs to know about a song. */
export interface PlayableTrack {
	service: Service | '';
	id: string;
	title: string;
	artist: string;
	artwork?: string | null;
	url?: string | null;
}

type Source = { kind: 'audio'; url: string } | { kind: 'youtube'; id: string };
type Status = 'loading' | 'playing' | 'paused' | 'ended' | 'error';

// TIDAL gives third-party apps no samples, so its songs aren't playable here
const ID_PATTERN: Partial<Record<Service, RegExp>> = {
	apple: /^\d{1,15}$/,
	spotify: /^[A-Za-z0-9]{22}$/,
	ytm: /^[A-Za-z0-9_-]{11}$/,
	deezer: /^\d{1,14}$/
};
const YT_ORIGIN = 'https://www.youtube-nocookie.com';

/** Only catalog songs have a sample (library-only Apple ids, local files … don't). */
export function canPlay(t: Pick<PlayableTrack, 'service' | 'id'> | null | undefined): t is PlayableTrack {
	return !!t && !!t.service && !!ID_PATTERN[t.service]?.test(t.id);
}

const keyOf = (t: Pick<PlayableTrack, 'service' | 'id'>) => `${t.service}:${t.id}`;

/**
 * One player for the whole app: clicking a cover plays its song, clicking it again pauses,
 * clicking another cover switches. Apple Music and Spotify give a 30 s sample, YouTube Music
 * plays the video through the embed (rendered by Player.svelte, steered here via postMessage).
 */
class PlayerState {
	track = $state<PlayableTrack | null>(null);
	source = $state<Source | null>(null);
	status = $state<Status>('loading');
	error = $state<string | null>(null);
	time = $state(0);
	duration = $state(0);

	#audio: HTMLAudioElement | null = null;
	#frame: HTMLIFrameElement | null = null;
	#seq = 0;
	#listenTimer: ReturnType<typeof setInterval> | null = null;
	#ytHeard = false;

	get key() {
		return this.track ? keyOf(this.track) : null;
	}

	isCurrent(t: Pick<PlayableTrack, 'service' | 'id'> | null | undefined) {
		return !!t && !!this.track && keyOf(t) === this.key;
	}

	isPlaying(t: Pick<PlayableTrack, 'service' | 'id'> | null | undefined) {
		return this.isCurrent(t) && (this.status === 'playing' || this.status === 'loading');
	}

	/** Cover click: start, pause or resume. */
	toggle(t: PlayableTrack) {
		if (!this.isCurrent(t)) return this.play(t);
		if (this.status === 'playing' || this.status === 'loading') this.pause();
		else if (this.status !== 'error') this.resume();
	}

	async play(t: PlayableTrack) {
		const seq = ++this.#seq;
		this.#halt();
		this.track = t;
		this.source = null;
		this.status = 'loading';
		this.error = null;
		this.time = 0;
		this.duration = 0;
		this.#mediaSession(t);
		let src: Source;
		try {
			src = await api<Source>(`/api/preview/${t.service}/${encodeURIComponent(t.id)}`);
		} catch (e) {
			if (seq !== this.#seq) return;
			this.status = 'error';
			this.error = (e as Error).message;
			return;
		}
		if (seq !== this.#seq) return;
		this.source = src;
		if (src.kind === 'audio') {
			const a = this.#ensureAudio();
			a.src = src.url;
			a.play().catch((e: DOMException) => {
				if (seq !== this.#seq || e.name === 'AbortError') return;
				// autoplay refused (e.g. iOS after a long fetch): leave it ready to start by hand
				this.status = e.name === 'NotAllowedError' ? 'paused' : 'error';
				if (this.status === 'error') this.error = tr('hear.cantPlay');
			});
		}
		// youtube: Player.svelte renders the iframe and calls attachFrame()
	}

	pause() {
		if (this.source?.kind === 'audio') this.#audio?.pause();
		else if (this.source?.kind === 'youtube') this.#yt('pauseVideo');
		this.status = 'paused';
	}

	resume() {
		if (this.status === 'ended') this.seek(0);
		if (this.source?.kind === 'audio') this.#audio?.play().catch(() => {});
		else if (this.source?.kind === 'youtube') this.#yt('playVideo');
	}

	seek(s: number) {
		this.time = s;
		if (this.source?.kind === 'audio' && this.#audio) this.#audio.currentTime = s;
		else if (this.source?.kind === 'youtube') this.#yt('seekTo', [s, true]);
	}

	close() {
		this.#seq++;
		this.#halt();
		this.track = null;
		this.source = null;
		if (typeof navigator !== 'undefined' && 'mediaSession' in navigator) navigator.mediaSession.metadata = null;
	}

	// ------------------------------------------------------------------ YouTube embed
	attachFrame(frame: HTMLIFrameElement | null) {
		this.#frame = frame;
		this.#ytHeard = false;
		if (this.#listenTimer) clearInterval(this.#listenTimer);
		this.#listenTimer = null;
		if (!frame) return;
		// the embed only reports back once asked to; keep asking until it is ready
		let tries = 0;
		const ask = () => {
			if (this.#ytHeard || ++tries > 40) {
				if (this.#listenTimer) clearInterval(this.#listenTimer);
				this.#listenTimer = null;
				return;
			}
			frame.contentWindow?.postMessage(JSON.stringify({ event: 'listening', id: 1, channel: 'widget' }), YT_ORIGIN);
		};
		ask();
		this.#listenTimer = setInterval(ask, 250);
	}

	onMessage = (e: MessageEvent) => {
		if (e.origin !== YT_ORIGIN || !this.#frame || e.source !== this.#frame.contentWindow) return;
		let d: any;
		try {
			d = typeof e.data === 'string' ? JSON.parse(e.data) : e.data;
		} catch {
			return;
		}
		this.#ytHeard = true;
		if (d.event === 'onError') {
			this.status = 'error';
			this.error = [101, 150].includes(Number(d.info))
				? tr('hear.ytOnly')
				: tr('hear.videoFailed');
		} else if (d.event === 'onStateChange') {
			this.#ytState(Number(d.info));
		} else if (d.event === 'infoDelivery' && d.info) {
			if (typeof d.info.currentTime === 'number') this.time = d.info.currentTime;
			if (typeof d.info.duration === 'number' && d.info.duration > 0) this.duration = d.info.duration;
			if (typeof d.info.playerState === 'number') this.#ytState(d.info.playerState);
		}
	};

	#ytState(s: number) {
		if (this.status === 'error') return;
		// -1 unstarted, 0 ended, 1 playing, 2 paused, 3 buffering, 5 cued
		if (s === 1) this.status = 'playing';
		else if (s === 2 || s === 5) this.status = 'paused';
		else if (s === 0) this.status = 'ended';
		else if (s === 3) this.status = 'loading';
	}

	#yt(func: string, args: unknown[] = []) {
		this.#frame?.contentWindow?.postMessage(JSON.stringify({ event: 'command', func, args, id: 1, channel: 'widget' }), YT_ORIGIN);
	}

	// ------------------------------------------------------------------ audio element
	#ensureAudio() {
		if (this.#audio) return this.#audio;
		const a = new Audio();
		a.preload = 'auto';
		const live = () => this.source?.kind === 'audio';
		a.addEventListener('playing', () => live() && (this.status = 'playing'));
		a.addEventListener('pause', () => live() && !a.ended && this.status !== 'error' && (this.status = 'paused'));
		a.addEventListener('waiting', () => live() && (this.status = 'loading'));
		a.addEventListener('ended', () => live() && (this.status = 'ended'));
		a.addEventListener('timeupdate', () => live() && (this.time = a.currentTime));
		a.addEventListener('durationchange', () => live() && isFinite(a.duration) && (this.duration = a.duration));
		a.addEventListener('error', () => {
			if (!live() || !a.getAttribute('src')) return;
			this.status = 'error';
			this.error = tr('hear.cantLoad');
		});
		this.#audio = a;
		return a;
	}

	#halt() {
		if (this.#audio) {
			this.#audio.pause();
			this.#audio.removeAttribute('src');
			this.#audio.load();
		}
		this.attachFrame(null);
	}

	#mediaSession(t: PlayableTrack) {
		if (typeof navigator === 'undefined' || !('mediaSession' in navigator)) return;
		try {
			navigator.mediaSession.metadata = new MediaMetadata({
				title: t.title,
				artist: t.artist,
				artwork: t.artwork ? [{ src: t.artwork }] : []
			});
			navigator.mediaSession.setActionHandler('play', () => this.resume());
			navigator.mediaSession.setActionHandler('pause', () => this.pause());
		} catch {
			// not essential
		}
	}
}

export const player = new PlayerState();

// songs known only by id (history, fade lists, the home shelf): the server picks a version with a sample
const songCache = new Map<number, Promise<PlayableTrack | null>>();
export function songTrack(songId: number): Promise<PlayableTrack | null> {
	let p = songCache.get(songId);
	if (!p) {
		p = api<PlayableTrack>(`/api/preview/song/${songId}`).catch(() => {
			songCache.delete(songId); // try again next time (offline, …)
			return null;
		});
		songCache.set(songId, p);
	}
	return p;
}
