# Competition rules

One readable XML baseline per competition. The same vocabulary drives the
competition page, result-entry phases and GPT tools. Curator edits are stored
as immutable database revisions, so a future rules change does not rewrite an
older edition.

A file is matched to a competition by `<name>`, through the archive's
normal club/competition name resolution. It never creates a competition.

`<phases>` is ordered scaffolding. Each phase has a stable `key`, display
`label`, FM-facing `stage`, and plain rule flags. League-specific blocks such
as `<promotionRelegation>` remain available to the table presentation.

Final tournaments can declare their calendar as well:

```xml
<calendar intervalYears="4" anchorYear="2022" editionKind="final_tournament" />
```

`intervalYears` and `anchorYear` prevent an accidental off-cycle edition from
being created. Qualifiers should be represented by a separate competition (for
example, `World Cup European Qualifying`). A one-off, explicitly sanctioned
exception can use `editionKind="special"`; it is never inferred from a
fixture date.
