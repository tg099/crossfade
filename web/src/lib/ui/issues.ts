// Songs Crossfade couldn't place for sure ("issues"): take a suggestion, paste a link, search
// again, ignore, or bring an ignored one back.
import { api, post } from '#lib/api.ts';
import { app } from '#lib/state.svelte.ts';
import type { Issue, TrackInfo } from '#lib/types.ts';
import { t } from './i18n.svelte.ts';
import { ui } from './ui.svelte.ts';

export const loadIssues = (fadeId: number) => api<Issue[]>(`/api/lines/${fadeId}/issues`);

/** target: a candidate id or a link / id pasted by the user */
export async function resolveIssue(fadeId: number, issue: Issue, target: string): Promise<boolean> {
	try {
		const r = await post<{ track: TrackInfo }>(`/api/lines/${fadeId}/issues/${issue.id}/resolve`, { target });
		ui.say(t('issue.matched', { title: r.track.title || issue.data.track.title }));
		app.reloadLine(fadeId);
		return true;
	} catch (e) {
		ui.fail(e);
		return false;
	}
}

export async function ignoreIssue(fadeId: number, issue: Issue, quiet = false): Promise<boolean> {
	try {
		await post(`/api/lines/${fadeId}/issues/${issue.id}/ignore`);
		if (!quiet) ui.say(t('issue.ignored', { title: issue.data.track.title }), 'x', 'info');
		app.reloadLine(fadeId);
		return true;
	} catch (e) {
		ui.fail(e);
		return false;
	}
}

/** after ignoring, the issue comes back under a new id: find it by song and collection */
export async function unignore(fadeId: number, issue: Issue): Promise<boolean> {
	try {
		const all = await loadIssues(fadeId);
		const ig = all.find((i) => i.kind === 'ignored' && i.song_id === issue.song_id && i.member_id === issue.member_id);
		if (ig) await post(`/api/lines/${fadeId}/issues/${ig.id}/restore`);
		app.reloadLine(fadeId);
		return true;
	} catch (e) {
		ui.fail(e);
		return false;
	}
}

export async function restoreIssue(fadeId: number, issue: Issue): Promise<boolean> {
	try {
		await post(`/api/lines/${fadeId}/issues/${issue.id}/restore`);
		ui.say(t('issue.restored', { title: issue.data.track?.title ?? '' }), 'undo', 'info');
		app.reloadLine(fadeId);
		return true;
	} catch (e) {
		ui.fail(e);
		return false;
	}
}

export async function researchIssue(fadeId: number, issue: Issue): Promise<TrackInfo[] | null> {
	try {
		const r = await post<{ candidates: TrackInfo[] }>(`/api/lines/${fadeId}/issues/${issue.id}/search`);
		if (!r.candidates.length) ui.say(t('issue.stillNothing'), 'search', 'info');
		return r.candidates;
	} catch (e) {
		ui.fail(e);
		return null;
	}
}

export const sameLength = (a: TrackInfo, b: TrackInfo) => !!a.duration && !!b.duration && Math.abs(a.duration - b.duration) < 3;
