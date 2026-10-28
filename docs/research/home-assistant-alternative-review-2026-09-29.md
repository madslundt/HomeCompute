# Home Assistant LLM alternatives and Danish fit

**Checked:** 2026-09-29  
**Status:** recommendation for a controlled Home Assistant comparison; no HA
instance changes made.

## Finding

The screenshot does not establish that model quality is the only problem. Two
answers explicitly report that the living-room climate entity and open-window
entities are not available to the agent. Home Assistant's current Assist
guidance says entities are opt-in, names/aliases and areas/floors must match
ordinary speech, and a binary window sensor needs `device_class: window` to
support an “is the window open?” query. Check those items before using answer
quality to judge a replacement. [Home Assistant Assist best practices](https://www.home-assistant.io/voice_control/best_practices).

The repository's active `home` route currently points at the dedicated
`google/gemma-4-E4B-it-qat-w4a16-ct` deployment named `home-fast`; its model
catalog marks it as Home-qualified. The 2026-09-29 host snapshot reports it
healthy alongside the active Qwen3.6-35B-A3B-NVFP4 deployment. The repository
does not contain a Home Assistant config export, so it cannot confirm the
conversation entity/pipeline selected for the screenshot. See
`config/model-catalog.json`, `config/capability-routes.json`, and
`docs/current-state.md`.

## Recommendation

1. **Fix/verify Home Assistant's exposed context first.** Confirm the climate
   entity is exposed to Assist and assigned to area `Stue`; confirm window
   contact sensors are exposed, have `device_class: window`, and have Danish
   names/aliases such as `vindue`, `åbent vindue`, and the floor/room names
   people use. Home Assistant also advises exposing only the entities needed,
   which avoids bloating model context. This likely resolves the two explicit
   “not found/not exposed” errors regardless of model choice.
2. **Best local model to A/B: the Qwen3.6-35B-A3B-NVFP4 service already on
   `home-spark`.** It is a 35B total / 3B active mixture-of-experts model,
   already serving through the repository's `automation-moe-nvidia` route on
   the same GB10. A Home Assistant trial can use a separately scoped
   credential and a temporary `home` candidate alias to that existing
   endpoint, without installing another model. This is a promising quality
   upgrade over the dedicated E4B model and preserves local-only inference.
   It is **not qualified yet** for Danish Home Assistant tool calls; the
   candidate must pass the same HA fixture and safety checks before promotion.
   NVIDIA documents vLLM/GB10 serving and structured tool parser configuration
   for the model, but its published accuracy table is general/agentic and does
   not test Danish entity lookup or Home Assistant. [NVIDIA Qwen3.6 model
   card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4), [Qwen3.6 base
   model card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B).
3. **Best direct Danish cloud comparison: Home Assistant's Google Gemini
   integration.** It has native Assist tool access, and Google currently
   lists Danish among Gemini's supported languages. This is the simplest
   official cloud comparison if local Qwen still disappoints. It sends prompts
   and relevant home state to Google's API and incurs API costs. The native
   integration's Google Search tool cannot be enabled at the same time as
   home-control function calling. [Home Assistant Gemini integration](https://www.home-assistant.io/integrations/google_generative_ai_conversation),
   [Gemini API languages and models](https://ai.google.dev/gemini-api/docs/models).
4. **OpenAI is another easy cloud benchmark**, through Home Assistant's
   official integration, but its integration only accepts the official OpenAI
   API endpoint (not the repository's LiteLLM proxy). It is paid. Anthropic
   also has an official integration; its documentation notes new provider
   model features can take up to two Home Assistant releases to arrive.
   [Home Assistant OpenAI integration](https://www.home-assistant.io/integrations/openai_conversation),
   [Home Assistant Anthropic integration](https://www.home-assistant.io/integrations/anthropic).

## Comparison approach

Use the same exposed Danish entity set, prompt, Assist mode, and questions for
Gemma 4 E4B, Qwen3.6-35B-A3B, and optionally Gemini. Include the screenshot's
three questions, known temperature values, open/closed windows on multiple
floors, ambiguous names, unavailable entities, and several safe device-control
requests. Record the exact tool and entities selected, answer correctness,
false claims/actions, latency, and whether it answers naturally in Danish.
Never promote a model that makes a false physical action or invents sensor
state. Home Assistant's Assist API only exposes the tools and entities selected
for Assist; Home Assistant remains the execution boundary. [Home Assistant
LLM API](https://developers.home-assistant.io/docs/core/llm/), [Assist best
practices](https://www.home-assistant.io/voice_control/best_practices).

If the complaint also concerns spoken conversation (rather than this typed
chat), assess Danish speech-to-text and text-to-speech separately: Home
Assistant configures those pipeline stages independently from the conversation
agent. [Create a personality with AI](https://www.home-assistant.io/voice_control/assist_create_open_ai_personality/).
