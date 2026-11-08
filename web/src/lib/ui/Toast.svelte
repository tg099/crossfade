<script lang="ts">
	import Glyph, { type GlyphName } from './Glyph.svelte';
	import { t } from './i18n.svelte.ts';
	import { Spring } from './motion.ts';
	import { ui } from './ui.svelte.ts';

	// Completion feedback as a small capsule that drops in from the top and leaves on its own.
	let p = $state(0);
	let shown = $state<{ text: string; icon: GlyphName; tone: string; undo?: () => void } | null>(null);
	const spring = new Spring(0, (v) => (p = v), 0.002);

	$effect(() => {
		const cur = ui.toast;
		if (cur) {
			shown = { text: cur.text, icon: cur.icon, tone: cur.tone, undo: cur.undo };
			spring.to(1, { response: 0.4, damping: 0.82 });
		} else spring.to(0, { response: 0.3 }).then((d) => d && (shown = null));
	});
</script>

<div class="toast-wrap" role="status" aria-live="polite">
	{#if shown}
		<div class="toast glass strong" class:fault={shown.tone === 'fault'} style:--p={p}>
			<button class="msg" onclick={() => (ui.toast = null)}>
				<Glyph name={shown.icon} size={18} weight={2.4} />
				<span>{shown.text}</span>
			</button>
			{#if shown.undo}
				<button
					class="undo lq"
					onclick={() => {
						shown?.undo?.();
						ui.toast = null;
						ui.say(t('toast.undone'), 'undo', 'info');
					}}><Glyph name="undo" size={16} weight={2.4} /> {t('common.undo')}</button
				>
			{/if}
		</div>
	{/if}
</div>

<style>
	.toast-wrap {
		position: fixed;
		top: calc(var(--safe-top) + 8px);
		left: 0;
		right: 0;
		z-index: 500;
		display: flex;
		justify-content: center;
		pointer-events: none;
	}
	.toast {
		pointer-events: auto;
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 6px 6px 6px 18px;
		min-height: 48px;
		border-radius: 999px;
		font-size: var(--f-sub);
		font-weight: 600;
		opacity: min(1, var(--p));
		transform: translateY(calc((1 - var(--p)) * -90px)) scale(calc(0.85 + var(--p) * 0.15));
	}
	.fault .msg :global(.glyph) {
		color: var(--red);
	}
	.msg {
		text-align: left;
		display: flex;
		align-items: center;
		gap: 8px;
		padding-right: 12px;
	}
	.undo {
		display: flex;
		align-items: center;
		gap: 5px;
		height: 36px;
		padding: 0 14px;
		border-radius: 999px;
		background: var(--label);
		color: var(--bg);
		font-weight: 700;
	}
	@media (prefers-reduced-motion: reduce) {
		.toast {
			transform: none;
		}
	}
</style>
