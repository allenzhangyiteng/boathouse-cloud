# Security

## Reporting

Use the repository’s **Security → Report a vulnerability** feature for private reports. Do not put secrets, account data or exploit details in public issues. If private reporting is unavailable, open an issue asking for a private reporting channel without disclosing the vulnerability.

## Trust model

The control plane has access to the Docker socket and manages databases, containers, volumes and encrypted application secrets. Treat control-plane and host access as administrative access. Use a dedicated host, restrict SSH and provider credentials, maintain patches, and protect the state directory and master key.

Tool containers have resource limits and separate networks. They are intended for trusted app builders, not hostile-code execution. Admin access to a tool includes source, secrets, deployment, sharing and deletion. Application authors must verify signed identity and enforce their own data-level permissions; see the hello example.

## Operating your own service

Generate fresh credentials for every installation. Configure your own email and optional registrar/payment providers. Make encrypted, access-controlled backups and test recovery. Never share logs or exports without reviewing their contents. The backup script exports sensitive database contents and state, so backups require the same protection as the running service.

The hosted-service terms and privacy copy bundled in the website are examples of product copy. Before offering your own service, replace them with policies and contact details appropriate to your operation.

## Publication checks

CI runs a file/content hygiene check and Gitleaks over the Git history. These checks reduce accidental disclosure and do not replace review. The initial public release was created from an allowlisted snapshot without the private development history, runtime state, internal business files, or recorded account screens.

## Security maintenance

The September 2026 maintenance update refreshes the web and cryptographic dependencies, restricts default database connection grants, protects control pages against framing, and uses a consistent SQLite backup with private archive permissions. A deployment operator must also restrict existing database PUBLIC grants and existing backup permissions after verifying legitimate readers. These updates do not confer a SOC 2 report, ISO 27001 certification or HIPAA compliance. Follow the trust model and commission an independent review before handling regulated or confidential customer data.
