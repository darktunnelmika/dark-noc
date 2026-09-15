// DARK NOC shared frontend state. Classic-script globals are intentional.
let state = {
  nodes: [], tunnels: [], incidents: [], plugins: [], deployments: [], certificates: [], traffic: [], monitors: [], fleetOperations: [],
  limits: { ssh_upload_bytes: null, ssh_relay_bytes: null }, sockets: new Map(), terminals: new Map(),
  hubVersion: null, paneCounter: 2, replayTimer: null,
  selectedNode: null, selectedTunnel: null, selectedIncident: null, selectedPlugin: 'dark-backhaul',
  nodeFilter: 'all', topologyFilter: 'all', topologySearch: '', editingNode: null, editingMonitor: null,
  files: { nodeId: null, path: '/root', parent: '/', entries: [], selected: null, editingPath: null }
};
let refreshInFlight = false;
let liveRefreshInFlight = false;
let liveSocket = null;
let activeUpload = null;
let activeRelay = null;
let authenticatedActivityTerminated = false;
