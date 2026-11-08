<script lang="ts">
	import '../app.css';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import Logo from '#lib/components/Logo.svelte';
	import { app } from '#lib/state.svelte.ts';
	import Toast from '#lib/ui/Toast.svelte';
	import { ui } from '#lib/ui/ui.svelte.ts';

	let { children } = $props();

	// pages that work without a session; everything else lives in the app shell ([...path])
	const PUBLIC = ['/login', '/setup'];
	const isPublic = $derived(PUBLIC.some((p) => page.url.pathname.startsWith(p)));

	ui.loadCached();
	app.boot();

	$effect(() => {
		if (!app.ready) return;
		const path = page.url.pathname;
		if (app.setupNeeded) {
			if (path !== '/setup') goto('/setup', { replaceState: true });
		} else if (!app.user) {
			if (!isPublic) goto(`/login?next=${encodeURIComponent(path + page.url.search)}`, { replaceState: true });
		} else if (app.user.must_change_password) {
			if (path !== '/password') goto('/password', { replaceState: true });
		}
	});

	// iOS only shows :active (press feedback) when a touch listener exists
	$effect(() => {
		const noop = () => {};
		document.addEventListener('touchstart', noop, { passive: true });
		return () => document.removeEventListener('touchstart', noop);
	});
</script>

{#if !app.ready}
	<div class="boot" aria-busy="true"><span class="mark"><Logo size={52} /></span></div>
{:else if isPublic || app.user}
	{@render children()}
{/if}
<Toast />

<style>
	.boot {
		min-height: 100dvh;
		display: grid;
		place-items: center;
	}
	.mark {
		animation: breathe 1.4s ease-in-out infinite alternate;
	}
	@keyframes breathe {
		from {
			transform: scale(0.94);
			opacity: 0.6;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.mark {
			animation: none;
		}
	}
</style>
