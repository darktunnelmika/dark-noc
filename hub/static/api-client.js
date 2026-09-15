// DARK NOC API/job client runtime. Classic-script globals are intentional.
async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  if (response.status === 401 && path !== '/api/auth/login') {
    terminateAuthenticatedActivity();
    throw new Error('Authentication required');
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail=Array.isArray(payload.detail)?payload.detail.map(item=>item.msg||JSON.stringify(item)).join(' · '):payload.detail;
    throw new Error(typeof detail==='string'?detail:`Request failed (${response.status})`);
  }
  return payload;
}

function dashboardLimits(summary) {
  const limits=summary.limits||{};
  return {
    ssh_upload_bytes:Number(limits.ssh_upload_bytes??summary.ssh_upload_limit??summary.ssh_upload_bytes)||null,
    ssh_relay_bytes:Number(limits.ssh_relay_bytes??summary.ssh_relay_limit??summary.ssh_relay_bytes)||null
  };
}

async function createJob(nodeId, kind, service = null, payload = {}, showOutput = false) {
  const job = await api(`/api/nodes/${nodeId}/jobs`, { method:'POST', body:JSON.stringify({kind,service,payload}) });
  showToast('COMMAND QUEUED', `Job #${job.job_id} will run through the secure Agent channel.`);
  if (showOutput) waitForJob(job.job_id);
  return job;
}

async function waitForJob(jobId) {
  $('#job-output').textContent = `Waiting for Agent to execute job #${jobId}…`;
  openModal('#output-modal');
  for (let attempt = 0; attempt < 180; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 1500));
    try {
      const job = await api(`/api/jobs/${jobId}`);
      if (job.status === 'completed' || job.status === 'failed') {
        $('#output-title').textContent = `${job.kind} · ${job.status.toUpperCase()}`;
        $('#job-output').textContent = job.output || 'Command completed without output.';
        return;
      }
      $('#job-output').textContent = `Job #${jobId}\nStatus: ${job.status}\nWaiting for Agent response…`;
    } catch (error) { $('#job-output').textContent = error.message; return; }
  }
  $('#job-output').textContent = 'No terminal result after 4.5 minutes. The job may still be running; check Remote job history.';
}
