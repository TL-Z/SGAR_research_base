# Third-party resources and license status

Runtime registries contain Models, Agents, Tools and vendored Skills. A service
descriptor or thin adapter does not distribute the remote model/service itself.
Service terms and source-code licenses are separate questions.

Skill manifests and `skills.lock.json` record pinned source repositories,
commits, package hashes and declared licenses. Existing LICENSE/NOTICE/OFL
files, fonts and package references are preserved byte for byte. Several
vendored packages have a license declaration without an included license file;
the external resource-license audit lists these as `LICENSE_REVIEW_REQUIRED`.
In particular, 56 registered `openai_plugins` Skill packages lack a license file
in their package ancestry. Their manifest's MIT declaration is not independent
verification or a replacement for the upstream notice.

Agent license notices remain under `Pool/resources/agents/LICENSES`. Font license
notices remain alongside their vendored packages. GPL, CC-BY-SA, service-specific
and other declarations are not collapsed into a single permissive license.
The framework owner must choose its own license and resolve missing notices
before representing the complete distribution as license-cleared open source.
No runtime resource was removed to avoid this review.
