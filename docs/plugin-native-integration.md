# DARK NOC Native Plugin Integration

DARK NOC does not expose a user-installed Plugin Store. Tunnel plugins are first developed and stabilized as standalone DARK tunnel projects, then integrated natively into DARK NOC.

## Integration flow

1. Build and stabilize the standalone tunnel project and installer.
2. Add a reviewed Hub manifest under `hub/plugins/<plugin-id>.json`.
3. Add the matching privileged Agent adapter entry for install, deploy, remove and inventory.
4. Reuse the generic Hub/UI behavior driven by manifest roles, transports, profiles, TLS ownership, Pair Code settings, endpoint semantics and `service_prefix`.
5. Add plugin-specific runtime code only when the tunnel genuinely requires behavior not represented by the existing contract.
6. Run Plugin Registry drift checks plus the full DARK NOC regression and release-build suite before merge.

This keeps plugin additions controlled by DARK NOC development while minimizing changes to the panel core.
