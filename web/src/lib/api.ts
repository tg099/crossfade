import { t } from './ui/i18n.svelte.ts';

export class ApiError extends Error {
	constructor(
		message: string,
		public status: number
	) {
		super(message);
	}
}

type Opts = { method?: string; body?: unknown; signal?: AbortSignal };

/** set by the app: a request found the session gone (expired, or signed out on another device) */
export const session: { lost: (() => void) | null } = { lost: null };

/** All requests carry X-Crossfade, which the server requires for state-changing calls (CSRF). */
export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
	let res: Response;
	try {
		res = await fetch(path, {
			method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
			headers: {
				'X-Crossfade': '1',
				...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {})
			},
			body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
			credentials: 'same-origin',
			signal: opts.signal
		});
	} catch (e) {
		// a cancelled request stays a cancellation; anything else means the server can't be reached
		if ((e as Error)?.name === 'AbortError') throw e;
		throw new ApiError(t('err.offline'), 0);
	}
	let data: any = null;
	const text = await res.text();
	if (text) {
		try {
			data = JSON.parse(text);
		} catch {
			data = text;
		}
	}
	if (!res.ok) {
		// the sign-in itself answers 401 for a wrong password: that is no lost session
		const lost = res.status === 401 && !path.startsWith('/api/auth/');
		if (lost) session.lost?.();
		let msg = t('err.status', { status: res.status });
		if (lost) msg = t('err.session');
		else if (data && typeof data.detail === 'string') msg = data.detail;
		else if (data && Array.isArray(data.detail)) msg = t('err.input');
		else if (res.status >= 502 && res.status <= 504) msg = t('err.offline');
		throw new ApiError(msg, res.status);
	}
	return data as T;
}

export const post = <T = any>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body });
export const put = <T = any>(path: string, body: unknown = {}) => api<T>(path, { method: 'PUT', body });
export const del = <T = any>(path: string) => api<T>(path, { method: 'DELETE' });
