# Product Boundary

`skill-router` is the routing and orchestration layer for individual operators.

It should help answer:

- what lane should this task use?
- which local capability is the best fit?
- what verification is needed before saying done?

It should not become:

- an enterprise governance platform
- a policy engine
- a silent installer
- a default internet crawler for random skills

If the user needs governance, policy, judgment scoring, or organizational proof,
consult the applicable project policies and approved governance tools. Do not
expand this routing skill beyond its boundary or infer permission for external actions.
