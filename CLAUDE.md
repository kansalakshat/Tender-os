# Tender project

Keywords the owner uses on the macOS fetching laptop:

- **"do setup"** -> use the `do-setup` skill (first-time setup, once).
- **"start fetching"** -> use the `start-fetching` skill (every day).

The production database is Neon's free plan: 5 GB of network transfer a month,
and running out takes the whole site down. Never select whole `tenders` rows
where a few columns will do, and never run the backlog scripts
(`gem_catchup.cmd`, `gem_enrich.cmd`) against production.
