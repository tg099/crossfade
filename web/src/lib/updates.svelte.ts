import { api, post } from './api.ts';

// Admins only: is there a newer Crossfade? The server asks GitHub twice a day.
export type UpdateState = {
	enabled: boolean;
	current: string;
	latest: string | null;
	url: string | null;
	checked_at: number | null;
	error: string | null;
	available: boolean;
};

const SEEN = 'crossfade.update.seen';

class Updates {
	state = $state<UpdateState | null>(null);
	checking = $state(false);
	/** the release whose notes were opened from the home page: its card goes away */
	seen = $state<string | null>(read());

	/** a card on the home page until the admin looked at what's new */
	fresh = $derived(!!this.state?.available && this.state.latest !== this.seen);

	async load() {
		try {
			this.state = await api<UpdateState>('/api/admin/update');
		} catch {
			// not essential
		}
	}

	async check() {
		this.checking = true;
		try {
			this.state = await post<UpdateState>('/api/admin/update/check');
		} finally {
			this.checking = false;
		}
	}

	open() {
		if (!this.state?.available) return;
		window.open(this.state.url ?? 'https://github.com/tg099/crossfade/releases', '_blank', 'noopener');
		this.seen = this.state.latest;
		try {
			localStorage.setItem(SEEN, this.seen ?? '');
		} catch {
			/* only a convenience */
		}
	}
}

function read(): string | null {
	try {
		return localStorage.getItem(SEEN);
	} catch {
		return null;
	}
}

export const updates = new Updates();
