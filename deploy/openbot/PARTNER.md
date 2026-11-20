# Private household OpenBot server

The partner's own OpenBot account owns `home-core-partner`. Its Compose project
has its own container, Docker bridge, and `/var/lib/homecompute-openbot-partner/data`.
The data directory holds the account session, agent workspaces, and the
embedded Hermes CLI and its `/data/.hermes` home. The guest administrator can
access this storage for operation and recovery.

The separate systemd service depends on its own egress guard. Its private
forwarding exceptions are Home Core DNS and the existing local model
compatibility API at `http://ai.home.arpa:18080/v1`. The guard also rejects
connections from the partner bridge to the agents VM itself. Public web
and the same OpenBot TURN relays are allowed for the client connection and
Hermes installation. The server has a 2 CPU and 4 GiB limit and publishes no
ports. The initial team uses `automation-moe` on the existing Home Spark
runtime. Start with empty MCP connections, provider credentials, and routines.

## Install and enroll

Publish the reviewed revision to `origin/master`. Make the same clean checkout
available under `/srv/homecompute/releases/FULL_SHA` on Home Core for backup
verification. If it has yet to be staged, follow the
[immutable checkout procedure](../../docs/notification-presentation-refresh.md)
through the detached checkout command. From the matching workstation checkout,
run:

```bash
./scripts/setup-openbot-guest.sh install-partner
./scripts/setup-openbot-guest.sh status-partner
./scripts/setup-openbot-guest.sh configure-partner-hermes
./scripts/setup-openbot-guest.sh login-partner
```

The installer verifies an encrypted agents-VM backup within the documented
readiness window and keeps the original OpenBot unit outside this guest
lifecycle. If a new backup is needed, use the attended VM backup in the
[OpenBot runbook](README.md), which briefly pauses the agents VM.

The partner enters their own email and verification code in the enrollment
terminal. Sign in to the desktop or web client with the same account and
select `home-core-partner`. Keep invitations empty for the private setup.

The Hermes helper runs the same pinned installer stages as the OpenBot 0.73.0
Hermes **Download** action. It verifies the installer checksum and configures the
CLI inside the partner container for local inference. In the signed-in client,
open the server's Providers page and confirm Hermes offers
`custom:automation-moe`. In the same server's Providers settings add
**Home AI (local)** as a custom OpenAI-compatible provider at
`http://ai.home.arpa:18080/v1` with model `automation-moe`. Leave the API key
empty or use `local` if the client requires a value.

## Agent team

Add the three agents below on `home-core-partner`. Keep their backup models
empty, or use the local model with the ask-first policy. Use **Ask first** for
permissions and turn off Computer Use and Local scripts at onboarding. Give
each agent its own workspace. Start with no schedules or action integrations.
Check the saved provider, model, and access in the client after reconnecting.

| Agent | Provider and model | Purpose |
| --- | --- | --- |
| Hermes | Hermes Agent, `custom:automation-moe` | Personal assistant and the only agent with the reviewed personal profile |
| Hverdagsplan | Home AI (local), `automation-moe` | Practical plans, routines, and prioritization |
| Skrivehjælp | Home AI (local), `automation-moe` | Message drafts and preparation for conversations |

Use the following instructions in each agent profile.

**Hermes**

> Svar på dansk som udgangspunkt. Vær varm, konkret og respektfuld. Giv et kort
> overblik og få realistiske næste skridt. Stil ét vigtigt spørgsmål ad gangen.
> Hjælp med planlægning og formuleringer, når jeg beder om det. Hold privat
> kontekst i denne samtale, og bed om godkendelse før deling, handling eller
> lagring af følsomme oplysninger.

**Hverdagsplan**

> Hjælp med at gøre ugens og dagens planer overskuelige. Foreslå en kort
> prioritering, del store opgaver op, og vis hvad der kan vente. Svar på dansk.
> Brug de oplysninger, jeg giver dig i samtalen. Bed om godkendelse, før du
> sender en besked, opretter en aftale eller starter en påmindelse.

**Skrivehjælp**

> Hjælp mig med at finde ordene på dansk. Lav et kort udkast, som jeg selv kan
> rette og godkende. Tilføj kun fakta, jeg har givet dig. Spørg, hvis en vigtig
> detalje mangler. Behold teksten som et udkast, indtil jeg beslutter, hvad
> der skal ske med den.

The local setup uses the compatibility model catalog. A future ChatGPT-backed
public research agent requires a separate, approved model-only connection to
LiteLLM with explicit cloud data rules and a verified workspace boundary.
Keep private profile files and personal context out of that workspace.

## Reviewed personal context

Give the partner the short Danish profile draft to edit and approve. Keep it
outside the repository as a local private file. A short profile can include
their preferred name, communication style, and how they want help. The source
attachment and confidential details stay outside the onboarding profile. Let
the partner decide what to disclose in their Hermes sessions.

Before the first personal session, load only the approved profile. It must be
UTF-8 and at most 1,300 characters:

```bash
./scripts/setup-openbot-guest.sh install-partner-profile /absolute/private/reviewed-user.md
```

This writes the reviewed text to the partner's private Hermes
`memories/USER.md` with mode 0600. Repeating the same import is safe.
The command refuses to overwrite different profile content. Open a new
Hermes conversation to see the reviewed profile in effect.

## Verify, stop, and recover

- Confirm the partner account can select `home-core-partner`. Check that the
  existing account and server still work with their original sign-in.
- Use synthetic data first. Run a short turn on each agent, then verify the
  partner's fresh Hermes session follows the approved communication preference
  without prompting it to recite or disclose the profile.
- Verify the selected Compose image, network, mount, and published ports in
  `status-partner` and Docker inspect. From the partner container confirm
  that the local model endpoint lists `automation-moe`, and that the Home
  Core MCP HTTPS, Android relay, and guest SSH ports are unreachable.
- For a service rollback, disable and stop only
  `homecompute-openbot-partner.service` in the guest. Keep the data and the
  immutable release bundle. Revoke the partner server session in OpenBot if
  withdrawing enrollment.
- Protect the whole partner data directory using the encrypted OpenBot data
  backup procedure or a verified full agents-VM snapshot. A code rollback
  preserves the dedicated data directory and cannot reverse a database
  migration. Use the verified pre-upgrade archive for data recovery.
