<script lang="ts">
	import { canPlay, player, songTrack, type PlayableTrack } from '#lib/player.svelte.ts';
	import { seedArt } from './artcolor.ts';
	import { t } from './i18n.svelte.ts';
	import { ui } from './ui.svelte.ts';

	// Cover art: the real artwork when there is one, otherwise a calm gradient derived from the
	// song, so lists keep their rhythm.
	// With `track` (or just a `song` id) the cover is the play button: a tap anywhere – also inside
	// a row, in selection mode, in a sheet – starts or pauses the preview, without opening the row.
	let {
		src = null,
		seed = '',
		size = 48,
		radius = 0.12,
		track = null,
		song = null
	}: {
		src?: string | null;
		seed?: string;
		size?: number | string;
		radius?: number;
		track?: PlayableTrack | null;
		song?: number | null;
	} = $props();

	let failed = $state(false);
	$effect(() => {
		src;
		failed = false;
	});
	const art = $derived(seedArt(seed || src || '♪'));
	const px = $derived(typeof size === 'number' ? `${size}px` : size);

	let resolved = $state<PlayableTrack | null>(null);
	let resolving = $state(false);
	$effect(() => {
		song;
		resolved = null;
	});
	const direct = $derived(canPlay(track) ? track : null);
	const playable = $derived(direct ?? resolved);
	const interactive = $derived(!!direct || song != null);
	const on = $derived(player.isPlaying(playable));
	const busy = $derived(resolving || (player.isCurrent(playable) && player.status === 'loading'));
	const label = $derived((track ?? playable)?.title ?? '');

	async function hit(e: Event) {
		e.preventDefault();
		e.stopPropagation();
		let tr = playable;
		if (!tr && song != null) {
			resolving = true;
			const id = song;
			tr = await songTrack(id);
			resolving = false;
			if (song !== id) return;
			resolved = tr && canPlay(tr) ? { ...tr, artwork: tr.artwork || src } : null;
			tr = resolved;
			if (!tr) {
				ui.say(t('hear.failed'));
				return;
			}
		}
		if (tr) player.toggle(tr);
	}
	function key(e: KeyboardEvent) {
		if (e.key === 'Enter' || e.key === ' ') hit(e);
	}
</script>

{#snippet artwork()}
	{#if src && !failed}
		<img class="cover" {src} alt="" loading="lazy" decoding="async" style:--s={px} style:--r={radius} onerror={() => (failed = true)} />
	{:else}
		<span
			class="cover"
			style:--s={px}
			style:--r={radius}
			style:background="radial-gradient(circle at {art.cx}% {art.cy}%, hsl({art.b} 85% 72% / 0.95), transparent 58%), linear-gradient(150deg, hsl({art.a} 62% 52%), hsl({art.c} 58% 24%))"
			aria-hidden="true"
		></span>
	{/if}
{/snippet}

{#if interactive}
	<!-- a span, not a button: covers sit inside row buttons, and buttons can't nest -->
	<span
		class="hit"
		class:on
		class:busy
		role="button"
		tabindex="0"
		style:--s={px}
		style:--r={radius}
		aria-pressed={on}
		aria-label={on ? t('hear.stopOne', { title: label }) : t('hear.one', { title: label })}
		onclick={hit}
		onkeydown={key}
	>
		{@render artwork()}
		<span class="ov" aria-hidden="true">
			{#if busy}
				<span class="spin"></span>
			{:else if on}
				<span class="eq"><i></i><i></i><i></i></span>
			{:else}
				<svg viewBox="0 0 24 24" class="play"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l11-6.5a1 1 0 0 0 0-1.72l-11-6.5A1 1 0 0 0 8 5.5z" fill="currentColor" /></svg>
			{/if}
		</span>
	</span>
{:else}
	{@render artwork()}
{/if}

<style>
	.cover {
		display: block;
		flex: none;
		width: var(--s);
		aspect-ratio: 1;
		object-fit: cover;
		border-radius: calc(var(--s) * var(--r));
		box-shadow: inset 0 0 0 0.5px rgb(0 0 0 / 0.1);
		background: var(--fill-3);
	}
	.hit {
		position: relative;
		display: block;
		flex: none;
		width: var(--s);
		border-radius: calc(var(--s) * var(--r));
		cursor: pointer;
		-webkit-tap-highlight-color: transparent;
	}
	.hit > :global(.cover) {
		width: 100%;
	}
	.hit:active {
		transform: scale(0.94);
		transition: transform 0.12s;
	}
	/* the play sign shows on hover (desktop); playing or loading it stays */
	.ov {
		position: absolute;
		inset: 0;
		display: grid;
		place-items: center;
		border-radius: inherit;
		background: rgb(0 0 0 / 0.38);
		color: #fff;
		opacity: 0;
		transition: opacity 0.15s;
		pointer-events: none;
	}
	.hit:hover .ov,
	.hit:focus-visible .ov,
	.hit.on .ov,
	.hit.busy .ov {
		opacity: 1;
	}
	.play {
		width: max(14px, min(34%, 40px));
		height: auto;
		filter: drop-shadow(0 1px 3px rgb(0 0 0 / 0.4));
	}
	.eq {
		display: flex;
		align-items: flex-end;
		gap: 2px;
		height: max(12px, min(30%, 36px));
	}
	.eq i {
		width: 3px;
		height: 100%;
		border-radius: 1px;
		background: currentColor;
		transform-origin: bottom;
		animation: eq 0.9s ease-in-out infinite alternate;
	}
	.eq i:nth-child(2) {
		animation-delay: -0.3s;
	}
	.eq i:nth-child(3) {
		animation-delay: -0.6s;
	}
	@keyframes eq {
		from {
			transform: scaleY(0.3);
		}
	}
	.spin {
		width: max(12px, min(28%, 32px));
		aspect-ratio: 1;
		border-radius: 50%;
		border: 2px solid currentColor;
		border-right-color: transparent;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	@media (hover: none) {
		.hit:hover:not(.on):not(.busy) .ov {
			opacity: 0;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.eq i,
		.spin {
			animation: none;
		}
		.hit:active {
			transform: none;
		}
	}
</style>
