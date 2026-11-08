// How a fade is doing, in words: the status line, notes per collection, its cover.
import { SERVICE_NAME } from '#lib/format.ts';
import type { Line, Member } from '#lib/types.ts';
import type { GlyphName } from './Glyph.svelte';
import { t, type Key } from './i18n.svelte.ts';
import { ago, clock } from './ui.svelte.ts';

/** symbols a fade can wear (chosen by the user instead of a number) */
export const FADE_ICONS: GlyphName[] = ['heart', 'car', 'coffee', 'sun', 'moon', 'dumbbell', 'headphones', 'note', 'star', 'sparkle', 'flame', 'leaf', 'plane', 'book', 'bolt', 'home'];

export const fadeIcon = (l: Pick<Line, 'icon'>): GlyphName => (FADE_ICONS.includes(l.icon as GlyphName) ? (l.icon as GlyphName) : 'note');

export type Tone = 'good' | 'attention' | 'fault' | 'quiet' | 'running';

export interface FadeStatus {
	tone: Tone;
	text: string;
	detail: string | null;
}

/** a collection that needs a new sign-in, or is not connected */
export function memberFault(m: Member): string | null {
	if (!m.account_connected) return t('member.disconnected', { service: SERVICE_NAME[m.service] });
	if (m.account_status === 'error') return t('member.expired', { service: SERVICE_NAME[m.service] });
	return null;
}

export function fadeStatus(l: Line): FadeStatus {
	const s = l.last_summary ?? {};
	const job = l.job;
	if (job) {
		return {
			tone: 'running',
			text: job.state === 'queued' ? t('status.queued') : job.mode === 'preview' ? t('status.previewing') : t('status.running'),
			detail: job.phase || null
		};
	}
	if (l.paused) return { tone: 'quiet', text: t('status.paused'), detail: null };
	const fault = l.members.map(memberFault).find(Boolean);
	if (fault) return { tone: 'fault', text: fault, detail: t('status.reconnect') };
	switch (l.last_status) {
		case 'auth_error':
			return { tone: 'fault', text: t('status.authError'), detail: s.message ?? null };
		case 'blocked':
			return { tone: 'fault', text: t('status.blocked'), detail: s.message ?? null };
		case 'error':
			return { tone: 'fault', text: t('status.failed'), detail: s.message ?? null };
		case 'needs_confirm':
			return { tone: 'attention', text: t('status.held', { n: s.held ?? 0 }), detail: t('status.heldDetail') };
	}
	if (!l.last_run_at) return { tone: 'quiet', text: t('status.never'), detail: t('status.startWithPreview') };
	const next = l.next_run_at ? t('status.next', { time: clock(l.next_run_at) }) : t('status.manual');
	if (l.issues > 0) return { tone: 'attention', text: t('home.waiting', { n: l.issues }), detail: `${t('status.synced', { ago: ago(l.last_run_at) })} ${next}` };
	if (l.last_status === 'partial') return { tone: 'attention', text: t('status.partial'), detail: s.message ?? next };
	return { tone: 'good', text: t('status.current', { ago: ago(l.last_run_at) }), detail: next };
}

/** artwork for the fade's mosaic: a few covers from each collection */
export function fadeCovers(l: Line): string[] {
	const out: string[] = [];
	for (let i = 0; i < 3; i++) for (const m of l.members) if (m.covers?.[i]) out.push(m.covers[i]);
	return [...new Set(out)].slice(0, 4);
}

export const toneColor: Record<Tone, string> = {
	good: 'var(--green)',
	attention: 'var(--orange)',
	fault: 'var(--red)',
	quiet: 'var(--label-3)',
	running: 'var(--tint-fill)'
};

/** accessible names of the fade colours (format.ts LINE_COLORS) */
export const COLOR_KEY: Record<string, Key> = {
	'#0072CE': 'color.blue',
	'#E4A100': 'color.mustard',
	'#C4007A': 'color.magenta',
	'#2E9E44': 'color.green',
	'#7A8B1E': 'color.olive',
	'#009CA6': 'color.teal',
	'#6A3FA0': 'color.violet',
	'#8C5A2B': 'color.brown'
};
