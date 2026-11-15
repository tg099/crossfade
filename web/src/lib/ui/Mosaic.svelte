<script lang="ts">
	import Cover from './Cover.svelte';

	// A fade's cover: four of its songs, like a playlist cover, edged in the fade's colour.
	// Without any covers yet it is simply the fade's colour, not four unrelated stand-ins.
	let { covers = [], seed = '', color, size = 160 }: { covers?: (string | null)[]; seed?: string; color: string; size?: number | string } = $props();
	const four = $derived([0, 1, 2, 3].map((i) => (covers.length ? covers[i % covers.length] : null)));
	const px = $derived(typeof size === 'number' ? `${size}px` : size);
</script>

<span class="mosaic" class:plain={!covers.length} style:--s={px} style:--c={color} aria-hidden="true">
	{#if covers.length}
		{#each four as src, i (i)}
			<Cover {src} seed="{seed}{i}" size="100%" radius={0} />
		{/each}
		<span class="edge"></span>
	{/if}
</span>

<style>
	.mosaic {
		position: relative;
		display: grid;
		grid-template-columns: 1fr 1fr;
		flex: none;
		width: var(--s);
		aspect-ratio: 1;
		border-radius: calc(var(--s) * 0.14);
		overflow: hidden;
		background: color-mix(in srgb, var(--c) 30%, var(--fill-3));
		box-shadow:
			0 1px 2px rgb(0 0 0 / 0.08),
			0 8px 20px -12px rgb(0 0 0 / 0.35);
	}
	.plain {
		background: linear-gradient(155deg, color-mix(in oklab, var(--c) 70%, white), color-mix(in oklab, var(--c) 86%, black));
	}
	/* the fade's colour runs along the bottom of the cover */
	.edge {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		height: max(3px, calc(var(--s) * 0.045));
		background: var(--c);
	}
</style>
