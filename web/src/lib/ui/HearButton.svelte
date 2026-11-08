<script lang="ts">
	import { canPlay, player, type PlayableTrack } from '#lib/player.svelte.ts';
	import Glyph from './Glyph.svelte';
	import { t } from './i18n.svelte.ts';

	// Listen in to one version (a short sample, not a player). Tap again to stop.
	// onArt: sits on top of artwork, so it brings its own dark glass
	let { track, size = 34, onArt = false }: { track: PlayableTrack; size?: number; onArt?: boolean } = $props();
	const playable = $derived(canPlay(track));
	const on = $derived(player.isPlaying(track));
	const loading = $derived(player.isCurrent(track) && player.status === 'loading');
</script>

{#if playable}
	<button
		type="button"
		class="hear lq"
		class:on
		class:onart={onArt}
		style:--s="{size}px"
		aria-pressed={on}
		aria-label={on ? t('hear.stopOne', { title: track.title }) : t('hear.one', { title: track.title })}
		onclick={(e) => {
			e.stopPropagation();
			if (on) player.close();
			else player.play(track);
		}}
	>
		{#if loading}
			<span class="spin" aria-hidden="true"></span>
		{:else if on}
			<span class="eq" aria-hidden="true"><i></i><i></i><i></i></span>
		{:else}
			<Glyph name="play" size={Math.round(size * 0.45)} />
		{/if}
	</button>
{/if}

<style>
	.hear {
		display: grid;
		place-items: center;
		flex: none;
		width: var(--s);
		height: var(--s);
		border-radius: 50%;
		background: var(--ctl);
		box-shadow: var(--ctl-shadow);
		color: var(--label);
	}
	.hear.onart {
		background: rgb(16 16 16 / 0.55);
		color: #fff;
		-webkit-backdrop-filter: blur(14px) saturate(1.6);
		backdrop-filter: blur(14px) saturate(1.6);
		box-shadow:
			0 4px 14px rgb(0 0 0 / 0.28),
			inset 0 0.5px 0 rgb(255 255 255 / 0.35);
	}
	.hear.on {
		background: var(--tint-fill);
		color: var(--on-tint);
	}
	.eq {
		display: flex;
		align-items: flex-end;
		gap: 2px;
		height: 40%;
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
		width: 40%;
		height: 40%;
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
	@media (prefers-reduced-motion: reduce) {
		.eq i,
		.spin {
			animation: none;
		}
	}
</style>
