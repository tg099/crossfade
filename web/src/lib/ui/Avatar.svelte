<script lang="ts">
	import { app } from '#lib/state.svelte.ts';
	import { userHue } from './artcolor.ts';
	import { t } from './i18n.svelte.ts';
	import { ui } from './ui.svelte.ts';

	// Profile button: opens your account. A red dot when a service needs a new sign-in.
	let { size = 40 }: { size?: number } = $props();
</script>

{#if app.user}
	<button
		class="avatar lq uav"
		style:--s="{size}px"
		style:--uh={userHue(String(app.user.id))}
		onclick={() => ui.openAccount()}
		aria-label={app.trouble ? t('account.openTrouble') : app.socialBadge ? `${t('account.open')}, ${t('friends.badge', { n: app.socialBadge })}` : t('account.open')}
	>
		{app.user.display_name.trim().charAt(0).toUpperCase() || '?'}
		{#if app.trouble || app.socialBadge}<span class="badge" aria-hidden="true"></span>{/if}
	</button>
{/if}

<style>
	.avatar {
		position: relative;
		display: grid;
		place-items: center;
		flex: none;
		width: var(--s);
		height: var(--s);
		border-radius: 50%;
		font-size: calc(var(--s) * 0.42);
		font-weight: 700;
		box-shadow:
			0 4px 14px -4px rgb(0 0 0 / 0.3);
	}
	.badge {
		position: absolute;
		top: 0;
		right: 0;
		width: 12px;
		height: 12px;
		border-radius: 50%;
		background: var(--red);
		box-shadow: 0 0 0 2.5px var(--bg);
	}
</style>
