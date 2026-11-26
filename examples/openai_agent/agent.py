"""Govern an OpenAI chat client with the support-triage policy.

Install the ``openai`` extra and set ``OPENAI_API_KEY`` before running this
file. Running it sends one chat completion to the OpenAI API.
"""

from pathlib import Path

from openai import OpenAI

from agent_tessera import Tessera

POLICY = Path(__file__).resolve().parents[2] / "policies" / "examples" / "support-triage.yaml"


def main() -> None:
    """Scan a prompt, read a ticket, and send one governed chat completion."""
    tessera = Tessera("support-triage", policy=POLICY)

    @tessera.guardrail("input.prompt_injection")
    def scan(prompt: str) -> str:
        """Record that the prompt-injection guardrail ran."""
        return prompt

    @tessera.tool("zendesk.read_ticket")
    def read_ticket(ticket_id: str) -> str:
        """Return a stand-in ticket body."""
        return f"ticket {ticket_id}"

    client = tessera.wrap(OpenAI())
    with tessera.task():
        scanned = scan("Where is order 1842?")
        print(read_ticket("1842"))
        reply = client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": scanned}],
        )
        print(reply.choices[0].message.content)
    tessera.close()


if __name__ == "__main__":
    main()
