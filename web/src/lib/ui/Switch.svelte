<script lang="ts">
	// Liquid Glass switch: while pressed the knob swells into a clear lens over the track.
	let { checked = $bindable(false), label }: { checked?: boolean; label: string } = $props();
</script>

<button type="button" class="sw" role="switch" aria-checked={checked} aria-label={label} onclick={() => (checked = !checked)}>
	<span class="knob"></span>
</button>

<style>
	.sw {
		position: relative;
		flex: none;
		width: 64px;
		height: 30px;
		border-radius: 999px;
		background: var(--fill);
		transition: background 260ms ease;
	}
	.sw[aria-checked='true'] {
		background: var(--tint-fill);
	}
	.knob {
		position: absolute;
		top: 2px;
		left: 2px;
		width: 38px;
		height: 26px;
		border-radius: 999px;
		background: #fff;
		box-shadow:
			0 3px 8px rgb(0 0 0 / 0.16),
			0 1px 1px rgb(0 0 0 / 0.06);
		transition:
			transform 480ms cubic-bezier(0.25, 1.5, 0.45, 1),
			background 200ms ease,
			box-shadow 200ms ease,
			scale 360ms cubic-bezier(0.25, 1.6, 0.45, 1);
	}
	.sw[aria-checked='true'] .knob {
		transform: translateX(22px);
	}
	/* pressed: the knob turns to glass and grows past the track */
	.sw:active .knob {
		scale: 1.35 1.45;
		background: rgb(255 255 255 / 0.28);
		-webkit-backdrop-filter: blur(2px) saturate(1.6);
		backdrop-filter: blur(2px) saturate(1.6);
		box-shadow:
			inset 0 1px 1px rgb(255 255 255 / 0.9),
			inset 0 -1px 2px rgb(255 255 255 / 0.4),
			inset 0 0 0 0.5px rgb(255 255 255 / 0.6),
			0 4px 12px rgb(0 0 0 / 0.14);
		transition-duration: 480ms, 120ms, 120ms, 220ms;
	}
	@media (prefers-reduced-motion: reduce) {
		.knob {
			transition: none;
		}
		.sw:active .knob {
			scale: none;
		}
	}
</style>
