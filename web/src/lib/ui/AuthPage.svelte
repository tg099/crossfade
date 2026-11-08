<script lang="ts">
	import type { Snippet } from 'svelte';
	import { about, loadAbout } from '#lib/about.svelte.ts';
	import Logo from '#lib/components/Logo.svelte';
	import { t } from './i18n.svelte.ts';

	// Sign-in, first setup, new password: one calm glass card on a soft colour field.
	let { title, intro = '', children }: { title: string; intro?: string; children: Snippet } = $props();
	loadAbout();
</script>

<div class="auth">
	<span class="ambient" aria-hidden="true"></span>
	<div class="brand">
		<Logo size={56} />
		<p class="name">Crossfade</p>
		<p class="claim t-sub t-2">{t('auth.claim')}</p>
	</div>
	<section class="card glass strong">
		<h1 class="t-title2">{title}</h1>
		{#if intro}<p class="t-sub t-2 intro">{intro}</p>{/if}
		{@render children()}
	</section>
	<p class="foot">
		Crossfade{about.version ? ` ${about.version}` : ''} ·
		<a href={about.source} target="_blank" rel="noopener noreferrer">{t('about.source')}</a>
	</p>
</div>

<style>
	.auth {
		position: relative;
		isolation: isolate;
		min-height: 100dvh;
		display: grid;
		align-content: center;
		justify-items: center;
		gap: 26px;
		padding: calc(var(--safe-top) + 32px) 16px calc(var(--safe-bottom) + 32px);
		overflow: hidden;
	}
	.ambient {
		position: fixed;
		inset: -10%;
		z-index: -1;
		height: auto;
		background:
			radial-gradient(40% 40% at 20% 15%, color-mix(in srgb, #ff3b4e 40%, transparent), transparent 70%),
			radial-gradient(45% 40% at 85% 20%, color-mix(in srgb, #2e6bff 38%, transparent), transparent 70%),
			radial-gradient(50% 45% at 55% 90%, color-mix(in srgb, #ff3b4e 26%, transparent), transparent 72%);
		filter: blur(10px);
	}
	.brand {
		display: grid;
		justify-items: center;
		gap: 6px;
		text-align: center;
	}
	.name {
		font-size: var(--f-t1);
		font-weight: 800;
		letter-spacing: var(--tr-t1);
	}
	.card {
		width: min(100%, 420px);
		display: grid;
		gap: 14px;
		padding: 26px 22px 22px;
		border-radius: 34px;
	}
	.intro {
		margin-top: -6px;
	}
	.foot {
		font-size: var(--f-foot);
		color: var(--label-2);
	}
	.foot a {
		color: inherit;
		text-decoration: underline;
		text-underline-offset: 2px;
	}
</style>
