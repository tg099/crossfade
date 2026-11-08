<script lang="ts">
	import Glyph from './Glyph.svelte';
	import { t, type Key } from './i18n.svelte.ts';
	import { ui, type Theme } from './ui.svelte.ts';

	// Appearance, as in iOS Settings: three little previews you can tap. Saved per user.
	const OPTIONS: { value: Theme; label: Key }[] = [
		{ value: 'system', label: 'theme.system' },
		{ value: 'light', label: 'theme.light' },
		{ value: 'dark', label: 'theme.dark' }
	];

	function key(e: KeyboardEvent) {
		const d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
		if (!d) return;
		e.preventDefault();
		const i = OPTIONS.findIndex((o) => o.value === ui.theme);
		const next = OPTIONS[(i + d + OPTIONS.length) % OPTIONS.length];
		ui.setTheme(next.value);
		(e.currentTarget as HTMLElement).querySelector<HTMLElement>(`[data-v="${next.value}"]`)?.focus();
	}
</script>

{#snippet phone(mode: 'light' | 'dark')}
	<span class="screen {mode}">
		<span class="bar"></span>
		<span class="card c1"></span>
		<span class="trow"><span class="dot"></span><span class="ln"></span></span>
		<span class="trow"><span class="dot"></span><span class="ln short"></span></span>
		<span class="dock"></span>
	</span>
{/snippet}

<div class="picker" role="radiogroup" aria-label={t('account.appearance')} tabindex="-1" onkeydown={key}>
	{#each OPTIONS as o}
		{@const on = ui.theme === o.value}
		<button
			type="button"
			class="opt"
			role="radio"
			aria-checked={on}
			tabindex={on ? 0 : -1}
			data-v={o.value}
			onclick={() => ui.setTheme(o.value)}
		>
			<span class="preview" class:on>
				{#if o.value === 'system'}
					<span class="split">
						<span class="half l">{@render phone('light')}</span>
						<span class="half r">{@render phone('dark')}</span>
					</span>
				{:else}
					{@render phone(o.value)}
				{/if}
			</span>
			<span class="tlabel">{t(o.label)}</span>
			<span class="radio" class:on aria-hidden="true">{#if on}<Glyph name="check" size={14} weight={3.2} />{/if}</span>
		</button>
	{/each}
</div>

<style>
	.picker {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 10px;
		padding: 18px 14px 14px;
		outline: none;
	}
	.opt {
		display: grid;
		justify-items: center;
		gap: 8px;
		border-radius: 18px;
	}
	.preview {
		position: relative;
		width: min(100%, 92px);
		aspect-ratio: 0.56;
		border-radius: 16px;
		padding: 3px;
		box-shadow: 0 0 0 1px var(--sep);
		transition:
			box-shadow 260ms ease,
			transform 420ms cubic-bezier(0.25, 1.5, 0.45, 1);
	}
	.opt:active .preview {
		transform: scale(0.95);
		transition-duration: 90ms;
	}
	.preview.on {
		box-shadow: 0 0 0 2.5px var(--tint-fill);
	}
	.split {
		position: relative;
		display: block;
		height: 100%;
		border-radius: 13px;
		overflow: hidden;
	}
	.half {
		position: absolute;
		inset: 0;
	}
	.half.r {
		clip-path: polygon(100% 0, 100% 100%, 0 100%);
	}
	.half :global(.screen),
	.split :global(.screen) {
		border-radius: 0;
	}

	/* a tiny phone screen in either scheme (fixed colours: it shows the other mode too) */
	.screen {
		position: relative;
		display: grid;
		align-content: start;
		gap: 6%;
		height: 100%;
		padding: 18% 10% 0;
		border-radius: 13px;
		overflow: hidden;
	}
	.screen.light {
		background:
			radial-gradient(80% 40% at 20% 0%, rgb(255 140 170 / 0.45), transparent 70%),
			radial-gradient(70% 40% at 100% 10%, rgb(120 190 255 / 0.4), transparent 70%),
			#fbfaf8;
		--ink: rgb(28 26 25 / 0.75);
		--soft: rgb(28 26 25 / 0.14);
		--glass: rgb(255 255 255 / 0.75);
	}
	.screen.dark {
		background:
			radial-gradient(80% 40% at 20% 0%, rgb(255 90 140 / 0.35), transparent 70%),
			radial-gradient(70% 40% at 100% 10%, rgb(80 150 255 / 0.3), transparent 70%),
			#0c0b0b;
		--ink: rgb(246 244 241 / 0.8);
		--soft: rgb(246 244 241 / 0.16);
		--glass: rgb(70 68 68 / 0.75);
	}
	.bar {
		width: 55%;
		height: 6px;
		border-radius: 3px;
		background: var(--ink);
	}
	.card {
		aspect-ratio: 2.1;
		border-radius: 8px;
		background: linear-gradient(140deg, #ff3b4e, #2e6bff);
	}
	.trow {
		display: flex;
		align-items: center;
		gap: 6%;
	}
	.dot {
		width: 16%;
		aspect-ratio: 1;
		border-radius: 4px;
		background: var(--soft);
	}
	.ln {
		flex: 1;
		height: 4px;
		border-radius: 2px;
		background: var(--soft);
	}
	.ln.short {
		flex: 0 0 45%;
	}
	.dock {
		position: absolute;
		left: 10%;
		right: 10%;
		bottom: 6%;
		height: 9%;
		border-radius: 999px;
		background: var(--glass);
		box-shadow: inset 0 0.5px 0 rgb(255 255 255 / 0.6);
	}
	.tlabel {
		font-size: var(--f-sub);
		font-weight: 600;
	}
	.radio {
		display: grid;
		place-items: center;
		width: 22px;
		height: 22px;
		border-radius: 50%;
		box-shadow: inset 0 0 0 1.5px var(--label-3);
		color: var(--on-tint);
		transition:
			background 200ms ease,
			box-shadow 200ms ease;
	}
	.radio.on {
		background: var(--tint-fill);
		box-shadow: none;
	}
	@media (prefers-reduced-motion: reduce) {
		.preview {
			transition: box-shadow 200ms ease;
		}
		.opt:active .preview {
			transform: none;
		}
	}
</style>
