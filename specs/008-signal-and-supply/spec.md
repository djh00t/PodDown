# Specification: Signal & Supply Integration

**Status:** Planned after core production quality is proven

## Goal

Use Signal & Supply as customer number one through ordinary PodDown extension
points, proving that the core remains domain-agnostic.

## Contract

The integration consists only of a versioned show profile, approved synthetic
speaker/voice assets and consents, finance/technology pronunciation lexicons,
disclosure policy, publishing target and GitHub workflow configuration. PodDown
core contains no ticker, market, portfolio, investment or Signal & Supply logic.

The lexicon covers company and executive names, tickers, exchanges, currencies,
percentages, valuation units, semiconductor terms and supply-chain terminology.
Numbers, dates, units, negation and investment qualifiers remain mandatory critical
tokens. Portfolio information not present in the canonical source package cannot
enter the script from memory or external context.

## Acceptance behavior

1. A Signal & Supply Markdown article renders using only public PodDown contracts.
2. Finance-specific critical tokens achieve 100% fidelity.
3. Counter-thesis and uncertainty survive adaptation without hype or invented facts.
4. Synthetic-presenter disclosure is present according to profile policy.
5. The same engine renders the non-finance robotics fixture unchanged.
6. Removing integration configuration leaves no finance-specific core code.

