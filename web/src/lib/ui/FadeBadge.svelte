<script lang="ts">
	import Glyph from './Glyph.svelte';
	import { fadeIcon } from './fade.ts';
	import { onColor } from './tint.ts';

	// A fade's own symbol, in its colour (picked by the user, no numbering).
	let { icon, color, size = 22 }: { icon: string; color: string; size?: number } = $props();
	const glyph = $derived(fadeIcon({ icon }));
</script>

<span class="badge" style:--s="{size}px" style:background={color} style:color={onColor(color)} aria-hidden="true">
	<Glyph name={glyph} size={Math.round(size * 0.62)} weight={2.3} />
</span>

<style>
	.badge {
		display: grid;
		place-items: center;
		flex: none;
		width: var(--s);
		height: var(--s);
		border-radius: calc(var(--s) * 0.32);
		box-shadow:
			inset 0 1px 0 rgb(255 255 255 / 0.35),
			0 1px 3px rgb(0 0 0 / 0.12);
	}
</style>
