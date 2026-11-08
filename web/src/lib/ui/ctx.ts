// Context menus: long press (touch), right click (mouse), or the context-menu key.
import { del, post } from '#lib/api.ts';
import { SERVICE_NAME, trackUrl } from '#lib/format.ts';
import { canPlay, player } from '#lib/player.svelte.ts';
import { app } from '#lib/state.svelte.ts';
import type { TrackInfo } from '#lib/types.ts';
import { t } from './i18n.svelte.ts';
import { tick10 } from './motion.ts';
import { nav, ui, type CtxItem, type CtxPreview } from './ui.svelte.ts';

type Spec = () => { preview: CtxPreview; items: CtxItem[] };

const HOLD_MS = 450;

/** Svelte action: `use:hold={() => ({ preview, items })}` */
export function hold(node: HTMLElement, spec: Spec) {
	let get = spec;
	let timer = 0;
	let start: { x: number; y: number } | null = null;
	let fired = false;
	node.style.setProperty('-webkit-touch-callout', 'none');
	node.style.setProperty('-webkit-user-select', 'none');
	node.style.userSelect = 'none';

	const open = () => {
		node.classList.remove('holding');
		if (ui.ctx) return;
		fired = true;
		tick10();
		const { preview, items } = get();
		ui.ctx = { el: node, rect: node.getBoundingClientRect(), preview, items };
	};
	const cancel = () => {
		clearTimeout(timer);
		node.classList.remove('holding');
		start = null;
	};
	const down = (e: PointerEvent) => {
		if (e.pointerType === 'mouse' || e.button !== 0) return;
		fired = false;
		start = { x: e.clientX, y: e.clientY };
		// the element sinks a little while you hold it: a hint that something is coming
		node.classList.add('holding');
		timer = window.setTimeout(open, HOLD_MS);
	};
	const move = (e: PointerEvent) => {
		if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 8) cancel();
	};
	const click = (e: MouseEvent) => {
		if (!fired) return;
		e.preventDefault();
		e.stopPropagation();
		fired = false;
	};
	const contextmenu = (e: MouseEvent) => {
		e.preventDefault();
		cancel();
		open();
	};
	const key = (e: KeyboardEvent) => {
		if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
			e.preventDefault();
			open();
		}
	};
	node.addEventListener('pointerdown', down);
	node.addEventListener('pointermove', move);
	node.addEventListener('pointerup', cancel);
	node.addEventListener('pointercancel', cancel);
	node.addEventListener('click', click, true);
	node.addEventListener('contextmenu', contextmenu);
	node.addEventListener('keydown', key);
	return {
		update(s: Spec) {
			get = s;
		},
		destroy() {
			cancel();
			node.removeEventListener('pointerdown', down);
			node.removeEventListener('pointermove', move);
			node.removeEventListener('pointerup', cancel);
			node.removeEventListener('pointercancel', cancel);
			node.removeEventListener('click', click, true);
			node.removeEventListener('contextmenu', contextmenu);
			node.removeEventListener('keydown', key);
		}
	};
}

export async function runFade(id: number, preview = false, allow_deletions = false) {
	try {
		await post(`/api/lines/${id}/run`, { preview, allow_deletions });
		await app.reloadLine(id);
	} catch (e) {
		ui.fail(e);
	}
}

export async function stopFade(id: number) {
	try {
		await post(`/api/lines/${id}/stop`);
	} catch (e) {
		ui.fail(e);
	}
}

export function askDeleteFade(id: number, then?: () => void) {
	const f = app.line(id);
	if (!f) return;
	ui.ask = {
		title: t('fade.deleteQ', { name: f.name }),
		message: t('fade.deleteMsg'),
		label: t('fade.delete'),
		run: async () => {
			try {
				await del(`/api/lines/${id}`);
				nav.forget(id);
				app.lines = app.lines.filter((x) => x.id !== id);
				ui.say(t('fade.deleted', { name: f.name }), 'trash');
				then?.();
			} catch (e) {
				ui.fail(e);
			}
		}
	};
}

/** a friend's fade: your collection leaves it, nothing is deleted anywhere */
export function askLeaveFade(id: number) {
	const f = app.line(id);
	if (!f) return;
	ui.ask = {
		title: t('share.leaveQ', { name: f.name }),
		message: t('share.leaveMsg'),
		label: t('share.leave'),
		run: async () => {
			try {
				await del(`/api/lines/${id}/membership`);
				nav.forget(id);
				app.lines = app.lines.filter((x) => x.id !== id);
				app.loadFriends();
				ui.say(t('share.left', { name: f.name }), 'logout');
			} catch (e) {
				ui.fail(e);
			}
		}
	};
}

export function fadeMenu(id: number): ReturnType<Spec> {
	const f = app.line(id)!;
	const items: CtxItem[] = f.job
		? [{ label: t('fade.stop'), icon: 'stop', run: () => stopFade(id) }]
		: [
				{ label: t('fade.syncNow'), icon: 'refresh', run: () => runFade(id) },
				{ label: t('fade.preview'), icon: 'eye', run: () => runFade(id, true) }
			];
	if (!f.mine) items.push({ label: t('share.leave'), icon: 'logout', role: 'destructive', run: () => askLeaveFade(id) });
	else
		items.push(
			{ label: f.paused ? t('fade.resume') : t('fade.pause'), icon: f.paused ? 'play' : 'pause', run: () => app.setPaused(id, !f.paused) },
			{ label: t('common.edit'), icon: 'edit', run: () => ui.edit(id) },
			{ label: t('fade.delete'), icon: 'trash', role: 'destructive', run: () => askDeleteFade(id) }
		);
	return { preview: { kind: 'fade', id }, items };
}

/** a song anywhere in the app; inside a fade it also offers the versions per collection */
export function trackMenu(track: TrackInfo, where: { fadeId?: number; songId?: number } = {}): ReturnType<Spec> {
	const items: CtxItem[] = [];
	if (canPlay(track)) {
		const on = player.isPlaying(track);
		items.push({ label: on ? t('hear.stop') : t('hear.in'), icon: 'ear', run: () => (on ? player.close() : player.play(track)) });
	}
	if (track.service) {
		const svc = track.service;
		items.push({
			label: t('ctx.openIn', { service: SERVICE_NAME[svc] }),
			icon: 'external',
			run: () => window.open(trackUrl(svc, track.id, track.url), '_blank', 'noopener')
		});
	}
	if (where.fadeId && where.songId) {
		const { fadeId, songId } = where;
		items.push({ label: t('song.versions'), icon: 'copy', run: () => ui.showSong(fadeId, songId) });
	}
	return { preview: { kind: 'track', track }, items };
}
