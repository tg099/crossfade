import { api } from '#lib/api.ts';

// Version and source code link of this instance. The AGPL (§13) asks every instance to offer its
// users the source of what they run, so the link is shown on the sign-in page and in the account.
const FALLBACK_SOURCE = 'https://github.com/tg099/crossfade';

export const about = $state({ version: '', source: FALLBACK_SOURCE });

let requested = false;
export function loadAbout() {
	if (requested) return;
	requested = true;
	api<{ version: string; source?: string }>('/api/health')
		.then((h) => {
			about.version = h.version;
			if (h.source) about.source = h.source;
		})
		.catch(() => (requested = false));
}
