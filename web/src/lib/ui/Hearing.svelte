<script lang="ts">
	import { player } from '#lib/player.svelte.ts';
	import Cover from './Cover.svelte';
	import Glyph from './Glyph.svelte';
	import { seedColors, trackColors } from './artcolor.ts';
	import { t } from './i18n.svelte.ts';
	import { Spring } from './motion.ts';
	import { ui } from './ui.svelte.ts';

	// Shown only while a sample plays: what you're listening in to, a ring that runs out, stop.
	// Not a player: no queue, nothing keeps playing. YouTube previews need the visible embed.
	let p = $state(0);
	let shown = $state(player.track);
	const spring = new Spring(0, (v) => (p = v), 0.002);
	$effect(() => {
		const c = player.track;
		if (c) {
			shown = c;
			spring.to(1, { response: 0.42, damping: 0.8 });
		} else spring.to(0, { response: 0.3 }).then((d) => d && (shown = null));
	});
	// a finished sample closes by itself
	$effect(() => {
		if (player.status === 'ended') player.close();
	});
	let colors = $state<string[]>([]);
	$effect(() => {
		const h = shown;
		if (!h) return;
		colors = seedColors(h.id);
		let live = true;
		trackColors({ id: h.id, artwork: h.artwork }).then((c) => live && (colors = c));
		return () => (live = false);
	});
	const frac = $derived(player.duration ? Math.min(1, player.time / player.duration) : 0);
	const yt = $derived(
		player.source?.kind === 'youtube'
			? `https://www.youtube-nocookie.com/embed/${encodeURIComponent(player.source.id)}?enablejsapi=1&autoplay=1&playsinline=1&rel=0&origin=${encodeURIComponent(location.origin)}`
			: null
	);
	function frame(node: HTMLIFrameElement) {
		const ready = () => player.attachFrame(node);
		node.addEventListener('load', ready);
		return () => {
			node.removeEventListener('load', ready);
			player.attachFrame(null);
		};
	}
	function key(e: KeyboardEvent) {
		if (e.key === 'Escape' && player.track && !(e.target as HTMLElement)?.closest?.('[role=dialog], input, textarea')) player.close();
	}
</script>

<svelte:window onmessage={player.onMessage} onkeydown={key} />

{#if shown}
	<!-- over: a sheet is open (versions are compared in one): float above it instead of hiding
	     behind it – at the top on a phone, where the sheet's choices and buttons stay free -->
	<div class="hearing-wrap" class:over={ui.sheets.length > 0} style:--p={p}>
		{#if yt}
			<div class="video glass strong">
				{#key yt}
					<iframe {@attach frame} src={yt} title={t('hear.video', { title: shown.title })} allow="autoplay; encrypted-media; picture-in-picture" referrerpolicy="strict-origin-when-cross-origin"></iframe>
				{/key}
			</div>
		{/if}
		<div class="hearing glass strong" style:--c1={colors[1]} role="status" aria-live="polite">
			<span class="ring" style:--f={frac} aria-hidden="true">
				<Cover src={shown.artwork} seed={shown.id} size={36} radius={0.5} track={shown} />
			</span>
			<span class="txt">
				<span class="lbl" class:err={player.status === 'error'}>{player.status === 'error' ? (player.error ?? t('hear.failed')) : t('hear.label')}</span>
				<span class="clip ttl">{shown.title} · {shown.artist}</span>
			</span>
			{#if player.status === 'paused'}
				<button class="ctl lq" onclick={() => player.resume()} aria-label={t('hear.resume')}><Glyph name="play" size={14} /></button>
			{/if}
			<button class="ctl lq" onclick={() => player.close()} aria-label={t('hear.stop')}><Glyph name="stop" size={14} /></button>
		</div>
	</div>
{/if}

<style>
	.hearing-wrap {
		position: fixed;
		z-index: 32;
		left: 50%;
		bottom: calc(var(--safe-bottom) + 10px + 64px + 12px);
		display: grid;
		gap: 8px;
		width: min(100vw - 32px, 420px);
		opacity: min(1, calc(var(--p) * 1.5));
		transform: translate(-50%, calc((1 - var(--p)) * 24px)) scale(calc(0.9 + var(--p) * 0.1));
	}
	.video {
		overflow: hidden;
		border-radius: 22px;
		aspect-ratio: 16 / 9;
	}
	iframe {
		display: block;
		width: 100%;
		height: 100%;
		border: 0;
	}
	.hearing {
		display: flex;
		align-items: center;
		gap: 10px;
		min-height: 56px;
		padding: 6px 8px 6px 10px;
		border-radius: 28px;
		background: color-mix(in srgb, var(--glass-strong) 84%, var(--c1, transparent) 16%);
	}
	/* the remaining time as a ring around the cover */
	.ring {
		display: grid;
		place-items: center;
		flex: none;
		width: 42px;
		height: 42px;
		border-radius: 50%;
		background: conic-gradient(var(--label) calc(var(--f) * 360deg), var(--fill) 0);
	}
	.ring :global(.cover) {
		border: 2px solid var(--glass-strong);
		box-sizing: content-box;
	}
	.txt {
		display: grid;
		flex: 1;
		min-width: 0;
	}
	.lbl {
		font-size: var(--f-cap);
		font-weight: 700;
		color: var(--label-2);
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	/* a sentence, not a label: calm case, at most two lines */
	.lbl.err {
		display: -webkit-box;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		overflow: hidden;
		text-transform: none;
		letter-spacing: 0;
		font-weight: 600;
		color: var(--red-text);
	}
	.ttl {
		font-size: var(--f-sub);
		font-weight: 600;
	}
	.ctl {
		display: grid;
		place-items: center;
		flex: none;
		width: 40px;
		height: 40px;
		border-radius: 50%;
		background: var(--ctl);
		box-shadow: var(--ctl-shadow);
		color: var(--label);
	}
	.hearing-wrap.over {
		z-index: 300;
		top: calc(var(--safe-top) + 8px);
		bottom: auto;
		transform: translate(-50%, calc((var(--p) - 1) * 24px)) scale(calc(0.9 + var(--p) * 0.1));
	}
	@media (min-width: 721px) {
		.hearing-wrap {
			left: calc(var(--side) + (100vw - var(--side)) / 2);
			bottom: 18px;
		}
		/* beside the centred sheet, in the corner */
		.hearing-wrap.over {
			top: auto;
			left: auto;
			right: 20px;
			bottom: 20px;
			width: min(100vw - 40px, 360px);
			transform: translate(0, calc((1 - var(--p)) * 24px)) scale(calc(0.9 + var(--p) * 0.1));
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.hearing-wrap,
		.hearing-wrap.over {
			transform: translate(-50%, 0);
		}
	}
	@media (prefers-reduced-motion: reduce) and (min-width: 721px) {
		.hearing-wrap.over {
			transform: none;
		}
	}
</style>
