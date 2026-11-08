<script lang="ts">
	// A labelled text field in glass, used by forms (sign-in, password, app credentials).
	let {
		label,
		value = $bindable(''),
		type = 'text',
		placeholder = '',
		autocomplete = 'off',
		hint = '',
		error = '',
		mono = false,
		disabled = false
	}: {
		label: string;
		value?: string;
		type?: string;
		placeholder?: string;
		autocomplete?: HTMLInputElement['autocomplete'];
		hint?: string;
		error?: string;
		mono?: boolean;
		disabled?: boolean;
	} = $props();
	const id = `f${Math.random().toString(36).slice(2, 8)}`;
</script>

<div class="fld">
	<label for={id}>{label}</label>
	<input {id} {type} bind:value {placeholder} {autocomplete} {disabled} class:mono aria-invalid={!!error} aria-describedby={hint || error ? `${id}h` : undefined} spellcheck="false" autocapitalize="off" />
	{#if error}<p class="msg err" id="{id}h">{error}</p>{:else if hint}<p class="msg" id="{id}h">{hint}</p>{/if}
</div>

<style>
	.fld {
		display: grid;
		gap: 6px;
	}
	label {
		padding: 0 6px;
		font-size: var(--f-foot);
		font-weight: 600;
		color: var(--label-2);
	}
	input {
		height: 48px;
		padding: 0 16px;
		border: 0;
		border-radius: 16px;
		background: var(--well);
		box-shadow: var(--well-shadow);
		color: var(--label);
		font: inherit;
		font-size: var(--f-body);
		outline: none;
		transition: box-shadow 160ms;
	}
	input:focus {
		box-shadow: inset 0 0 0 2px color-mix(in srgb, var(--tint-fill) 60%, #0a84ff);
	}
	input[aria-invalid='true'] {
		box-shadow: inset 0 0 0 1.5px var(--red);
	}
	.mono {
		font-family: ui-monospace, 'SF Mono', Menlo, monospace;
		font-size: var(--f-sub);
	}
	.msg {
		padding: 0 6px;
		font-size: var(--f-foot);
		color: var(--label-2);
	}
	.err {
		color: var(--red-text);
	}
</style>
