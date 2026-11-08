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

/** All requests carry X-Crossfade, which the server requires for state-changing calls (CSRF). */
export async function api<T = any>(path: string, opts: Opts = {}): Promise<T> {
	const res = await fetch(path, {
		method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
		headers: {
			'X-Crossfade': '1',
			...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {})
		},
		body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
		credentials: 'same-origin',
		signal: opts.signal
	});
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
		let msg = t('err.status', { status: res.status });
		if (data && typeof data.detail === 'string') msg = data.detail;
		else if (data && Array.isArray(data.detail)) msg = t('err.input');
		throw new ApiError(msg, res.status);
	}
	return data as T;
}

export const post = <T = any>(path: string, body: unknown = {}) => api<T>(path, { method: 'POST', body });
export const put = <T = any>(path: string, body: unknown = {}) => api<T>(path, { method: 'PUT', body });
export const del = <T = any>(path: string) => api<T>(path, { method: 'DELETE' });
