// DARK NOC forced tunnel inventory scan. Classic-script globals are intentional.
async function scanTunnelInventory(trigger = null) {
  if (scanTunnelInventory.running) return;
  const online = state.nodes.filter(node => node.status === 'online');
  if (!online.length) {
    showToast('NO ONLINE AGENTS', 'Tunnel scan was not queued.', true);
    return;
  }
  scanTunnelInventory.running = true;
  const controls = $$('[data-scan-tunnels]');
  controls.forEach(control => {
    control.disabled = true;
    control.setAttribute('aria-busy', 'true');
  });
  try {
    const results = await Promise.allSettled(online.map(node => api(
      `/api/nodes/${Number(node.id)}/jobs`,
      { method: 'POST', body: JSON.stringify({ kind: 'tunnel_scan', payload: {} }) }
    )));
    const queued = results.filter(result => result.status === 'fulfilled').length;
    if (!queued) throw new Error('No tunnel scan job could be queued');
    const modal = trigger?.closest('.modal-backdrop');
    if (modal) closeModal(modal);
    showToast(
      'TUNNEL SCAN QUEUED',
      `${queued}/${online.length} online Agent${online.length === 1 ? '' : 's'} will force-refresh DARK tunnel inventory.`
    );
    clearTimeout(scanTunnelInventory.fastRefresh);
    clearTimeout(scanTunnelInventory.finalRefresh);
    scanTunnelInventory.fastRefresh = setTimeout(() => refreshLive(), 3000);
    scanTunnelInventory.finalRefresh = setTimeout(
      () => refreshActiveView('overview', { force: true }),
      12000
    );
  } catch (error) {
    showToast('TUNNEL SCAN FAILED', error.message, true);
  } finally {
    scanTunnelInventory.running = false;
    controls.forEach(control => {
      control.disabled = false;
      control.removeAttribute('aria-busy');
    });
  }
}

$$('[data-scan-tunnels]').forEach(button => {
  button.addEventListener('click', () => scanTunnelInventory(button));
});
