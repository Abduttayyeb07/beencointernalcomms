# Wazuh Integration (optional, not built by default)

This SIEM integration is not part of the running app — no Wazuh admin portal, no API routes, no
database table for it. It was in the original project scope and later dropped; the app only
emits a plain JSON audit stream on stdout, which anyone can point at any log pipeline or SIEM.
This document is kept for reference in case an operator specifically wants to wire that stream
into a real Wazuh manager.

Install the Wazuh agent on each portal host and configure a `localfile` entry for the API audit
stream, for example:

```xml
<localfile>
  <log_format>json</log_format>
  <location>/var/log/beenco/audit.json</location>
</localfile>
```

Copy `decoders.xml` into the manager's custom decoder directory and `rules.xml` into the custom
rules directory, then restart the manager. The API also emits audit JSON to stdout so container
logs can be collected by Loki and the Wazuh agent.
