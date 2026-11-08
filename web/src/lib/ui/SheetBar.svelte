<script lang="ts">
	import Glyph from './Glyph.svelte';

	// Header of a sheet: glass ✕ / ‹ on the left, title, tinted glass ✓ on the right.
	let {
		title,
		leading,
		confirm
	}: {
		title: string;
		leading?: { label: string; icon?: 'x' | 'chevronLeft'; run: () => void };
		confirm?: { label: string; run: () => void; disabled?: boolean };
	} = $props();
</script>

<div class="sbar">
	<div class="side">
		{#if leading}
			<button class="gbtn round glass lq" onclick={leading.run} aria-label={leading.label} title={leading.label}>
				<Glyph name={leading.icon ?? 'x'} size={leading.icon === 'chevronLeft' ? 22 : 18} weight={2.6} />
			</button>
		{/if}
	</div>
	<p class="title clip">{title}</p>
	<div class="side end">
		{#if confirm}
			<button class="gbtn round glass tint lq" onclick={confirm.run} disabled={confirm.disabled} aria-label={confirm.label} title={confirm.label}>
				<Glyph name="check" size={20} weight={2.8} />
			</button>
		{/if}
	</div>
</div>

<style>
	.sbar {
		display: grid;
		grid-template-columns: 52px 1fr 52px;
		align-items: center;
		gap: 8px;
		padding: 4px 14px 10px;
	}
	.side {
		display: flex;
	}
	.end {
		justify-content: flex-end;
	}
	.title {
		text-align: center;
		font-size: var(--f-body);
		font-weight: 700;
	}
	.sbar :global(.gbtn:disabled) {
		opacity: 1;
		background: var(--fill-3);
		color: var(--label-3);
	}
</style>
