<script lang="ts">
	let { size = 32 }: { size?: number } = $props();
</script>

<!-- Crossfade: a red curve fades out while a blue one fades in; flat colours, no gradients.
     The wide tile-coloured stroke under the blue curve cuts a gap into the red one where they cross.
     Colours follow the theme (--mark-* in app.css): white tile in light mode, ink tile in dark mode. -->
<svg class="logo" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
	<rect class="tile" x="0.25" y="0.25" width="31.5" height="31.5" rx="8.75"/>
	<path class="out" d="M7 10C14 10 18 22 25 22"/>
	<path class="gap" d="M7 22C14 22 18 10 25 10"/>
	<path class="in" d="M7 22C14 22 18 10 25 10"/>
</svg>

<style>
	.logo {
		flex: none;
		display: block;
		filter: drop-shadow(0 2px 5px rgb(0 0 0 / 0.18));
	}
	.tile {
		fill: var(--mark-bg);
		stroke: var(--mark-rim);
		stroke-width: 0.5;
	}
	path {
		fill: none;
		stroke-width: 3.4;
		stroke-linecap: round;
	}
	.out {
		stroke: var(--mark-red);
	}
	.gap {
		stroke: var(--mark-bg);
		stroke-width: 6.6;
	}
	.in {
		stroke: var(--mark-blue);
	}
</style>
