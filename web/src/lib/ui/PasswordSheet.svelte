<script lang="ts">
	import { put } from '#lib/api.ts';
	import Sheet from './Sheet.svelte';
	import SheetBar from './SheetBar.svelte';
	import { t } from './i18n.svelte.ts';
	import { ui } from './ui.svelte.ts';

	// Change your own password; other devices are signed out.
	let current = $state('');
	let next = $state('');
	let again = $state('');
	let busy = $state(false);
	let error = $state('');
	const problem = $derived(
		!next ? '' : next.length < 8 ? t('password.short') : next === current ? t('password.same') : again && again !== next ? t('password.mismatch') : ''
	);
	const valid = $derived(!!current && next.length >= 8 && next === again && next !== current);

	async function save() {
		if (!valid || busy) return;
		busy = true;
		error = '';
		try {
			await put('/api/auth/me/password', { current, new: next });
			ui.passwordOpen = false;
			ui.say(t('password.changed'), 'key');
		} catch (e) {
			error = (e as Error).message;
		} finally {
			busy = false;
		}
	}
</script>

<Sheet bind:open={ui.passwordOpen} label={t('account.password')} onclosed={() => (current = next = again = error = '')}>
	{#snippet header()}
		<SheetBar title={t('account.password')} leading={{ label: t('common.cancel'), run: () => (ui.passwordOpen = false) }} confirm={{ label: t('common.save'), run: save, disabled: !valid || busy }} />
	{/snippet}
	<form class="wrap" onsubmit={(e) => (e.preventDefault(), save())}>
		<div class="section">
			<div class="group">
				<label class="cell">
					<span class="lbl">{t('password.current')}</span>
					<input class="in" type="password" bind:value={current} autocomplete="current-password" required />
				</label>
			</div>
		</div>
		<div class="section">
			<div class="group">
				<label class="cell">
					<span class="lbl">{t('password.new')}</span>
					<input class="in" type="password" bind:value={next} autocomplete="new-password" minlength="10" required />
				</label>
				<label class="cell">
					<span class="lbl">{t('password.again')}</span>
					<input class="in" type="password" bind:value={again} autocomplete="new-password" required />
				</label>
			</div>
			{#if problem}<p class="section-foot warn">{problem}</p>{:else}<p class="section-foot">{t('password.hint')}</p>{/if}
			{#if error}<p class="section-foot danger" role="alert">{error}</p>{/if}
		</div>
		<button hidden aria-hidden="true"></button>
	</form>
</Sheet>

<style>
	.wrap {
		padding: 12px 0 calc(var(--safe-bottom) + 40px);
	}
	.lbl {
		width: 8.5em !important;
	}
</style>
