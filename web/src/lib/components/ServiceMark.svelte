<script lang="ts">
	import type { Service } from '#lib/types.ts';
	import { SERVICE_NAME } from '#lib/format.ts';

	let { service, size = 18, title = true }: { service: Service | ''; size?: number; title?: boolean } = $props();
</script>

<!-- Neutral glyphs in the services' colours (not their logos). -->
<svg
	class="mark"
	width={size}
	height={size}
	viewBox="0 0 20 20"
	role={title ? 'img' : 'presentation'}
	aria-label={title && service ? SERVICE_NAME[service] : undefined}
	aria-hidden={title ? undefined : 'true'}
>
	{#if service === 'ytm'}
		<rect width="20" height="20" rx="6" fill="#E8272C" />
		<circle cx="10" cy="10" r="5.6" fill="none" stroke="#fff" stroke-width="1.6" />
		<path d="M8.6 7.6v4.8l4-2.4z" fill="#fff" />
	{:else if service === 'apple'}
		<rect width="20" height="20" rx="6" fill="#F2345A" />
		<path d="M8.2 13.4V6.6l5.6-1.2v6.6" fill="none" stroke="#fff" stroke-width="1.6" stroke-linejoin="round" />
		<circle cx="7" cy="13.6" r="1.8" fill="#fff" />
		<circle cx="12.6" cy="12.2" r="1.8" fill="#fff" />
	{:else if service === 'spotify'}
		<rect width="20" height="20" rx="6" fill="#1ED760" />
		<path d="M5.4 8.2c3.2-1.1 6.6-.8 9.4.8M6 10.9c2.6-.8 5.2-.5 7.4.7M6.6 13.4c2-.5 3.9-.3 5.6.6" fill="none" stroke="#0B2A16" stroke-width="1.5" stroke-linecap="round" />
	{:else if service === 'tidal'}
		<rect width="20" height="20" rx="6" fill="#111" />
		<path d="M4.6 8.4c1.8-1.6 3.6-1.6 5.4 0s3.6 1.6 5.4 0M4.6 11.8c1.8-1.6 3.6-1.6 5.4 0s3.6 1.6 5.4 0" fill="none" stroke="#fff" stroke-width="1.5" stroke-linecap="round" />
	{:else if service === 'deezer'}
		<rect width="20" height="20" rx="6" fill="#A238FF" />
		<circle cx="10" cy="10" r="5.4" fill="none" stroke="#fff" stroke-width="1.5" />
		<circle cx="10" cy="10" r="1.5" fill="#fff" />
	{:else}
		<rect width="20" height="20" rx="6" fill="var(--grout)" />
	{/if}
</svg>

<style>
	.mark {
		flex: none;
		display: inline-block;
		vertical-align: -0.2em;
	}
</style>
