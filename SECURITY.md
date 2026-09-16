# Security

gradgate can sign transactions with a private key you give it, so a bug here can cost someone money.

**Please do not open a public issue for a vulnerability.** Report it privately through GitHub's
"Report a vulnerability" (Security → Advisories) on this repository, with what you found and how to reproduce it.

In scope: anything that lets another machine change strategies or the kill switch, reach the private key, place or
alter a live order, or make the engine sign something the user did not configure.

Out of scope: losses from strategies behaving as written, and RPC providers' own limits or outages.
