# Catan Bot Reference

## Constants

### Resources
| ID | Type    |
|----|---------|
| 0  | Desert  |
| 1  | Lumber  |
| 2  | Brick   |
| 3  | Wool    |
| 4  | Grain   |
| 5  | Ore     |

### Players
| Color | ID |
|-------|----|
| Bank  | 0  |
| Blue  | 1  |
| Red   | 2  |

### Board
- **54** vertices (settlements / cities)
- **72** edges (roads)
- **19** hex tiles

### Sequences
- `in_sequence` — WS IN message counter, starts at 1, +1 per message
- `out_sequence` — WS OUT message counter, starts at 1, +1 per message
- `id` — game ID, consistent all game (e.g. `"130"`)

---

## WS IN Messages

All messages carry an incrementing `in_sequence`.

---

### Type 1 — Game Settings
Lists match settings. See `type1.json`.

---

### Type 4 — Full Game State Snapshot
> **Source of truth.** Always triggers a full state reset in the bot, regardless of game ID.

Sent on connect / reconnect. Contains the same fields as Type 91 diffs at the top level of `payload`:

| Path | Contents |
|------|----------|
| `payload.gameState.mapState.tileHexStates` | Hex resource types + dice numbers |
| `payload.gameState.mapState.tileCornerStates` | Placed settlements / cities |
| `payload.gameState.mapState.tileEdgeStates` | Placed roads |
| `payload.gameState.mapState.portEdgeStates` | Port locations + trade ratios (see below) |
| `payload.gameState.currentState` | `turnState`, `currentTurnPlayerColor` |
| `payload.gameState.tradeState.activeOffers` | Active trade offers |

#### `portEdgeStates`
Only present in Type 4 (ports never change mid-game, so Type 91 diffs never touch this).
```json
{
  "0": { "x": 0, "y": -2, "z": 0, "type": 6 },
  "1": { "x": -2, "y": 2, "z": 2, "type": 1 }
}
```
- Keyed `"0"`–`"8"`, one entry per port. `x`/`y`/`z` are cube hex coordinates for the port's edge (unused by the bot — see below for the vertex mapping instead).
- `type` — trade ratio / resource:

| `type` | Meaning |
|--------|---------|
| `1` | Generic 3:1 |
| `2` | Lumber 2:1 |
| `3` | Brick 2:1 |
| `4` | Wool 2:1 |
| `5` | Grain 2:1 |
| `6` | Ore 2:1 |

- Port **index** (`"0"`–`"8"`) → vertex pair is fixed across every game (only `type` is randomized per game), hand-mapped in `catan_bot/data/graph_data/port_to_vertex.json`. `GameState.parse_board` uses that file to build `state.ports: {vertex_id: {"resource": rsc_id_or_None, "ratio": 2_or_3}}` (`resource=None` for generic 3:1 ports).

---

### Type 6 — Unknown
Boolean payload. Purpose unknown. Ignored by bot.

---

### Type 18 — Card Selection Prompt *(dual purpose)*
Sent after playing a Monopoly or Year of Plenty dev card. Distinguish by `developmentCardUsed`
(`13` = monopoly, `15` = year of plenty) or by `selectCardFormat.amountOfCardsToSelect` (`1` vs `2`).

**Monopoly** (action 48 payload `13`):
```json
{
  "developmentCardUsed": 13,
  "selectCardFormat": {
    "amountOfCardsToSelect": 1,
    "validCardsToSelect": [1, 2, 3, 4, 5]
  }
}
```
- `validCardsToSelect` — always all 5 resource types
- Bot responds with **Action 7**, payload = single-element array of chosen resource, e.g. `[4]`
- Same action as discard but single element; game distinguishes by context

**Year of Plenty** (action 48 payload `15`):
```json
{
  "developmentCardUsed": 15,
  "selectCardFormat": {
    "amountOfCardsToSelect": 2,
    "validCardsToSelect": [1, 2, 3, 4, 5]
  }
}
```
- Bot responds with **Action 7**, payload = 2-element array of chosen resources, e.g. `[4, 5]` (duplicates allowed — takes 2 of the same resource from the bank)
- No opponent component (unlike Monopoly) — picks purely by unmet build need

---

### Type 13 — Discard Prompt
Sent when a player holds 7+ cards after a 7 is rolled.

```json
{
  "selectCardFormat": {
    "validCardsToSelect": [3, 2, 3, 5],
    "amountOfCardsToSelect": 2
  }
}
```

- `validCardsToSelect` — list of card IDs currently in hand
- `amountOfCardsToSelect` — number of cards that must be discarded
- Bot responds with **Action 7** only (Action 8 not needed)

---

### Type 28 — Resource Distribution
Shows who received a resource and why.

- `distributionType`: `0` = initial placement, `1` = dice roll
- May be empty or omit enemy card details

---

### Type 29 — Choose Player to Steal From
Sent after placing the robber on a hex with multiple opponents (post dice-roll-7 flow).

```json
{
  "playersToSelect": [2, 3],
  "isPirate": false
}
```

- `playersToSelect` — list of player colors that can be stolen from, top-level
- `isPirate` — `false` for land robber, `true` for pirate (Seafarers)
- Bot responds with **Action 5**, payload = chosen player color

---

### Type 20 — Choose Player to Steal From (Knight)
Same steal-selection prompt as Type 29, but sent after playing a Knight dev card pre-roll
(action 48 payload `11`) instead of after a dice-roll 7. See `type20.json`.

```json
{
  "developmentCardUsed": 11,
  "selectPlayerFormat": {
    "playersToSelect": [3, 2],
    "isPirate": false
  }
}
```

- `playersToSelect` — nested under `selectPlayerFormat`, unlike Type 29's top-level field
- `developmentCardUsed` — `11` confirms this is the Knight-triggered prompt
- Bot responds with **Action 5**, payload = chosen player color (same as Type 29)

---

### Type 30 — Available Vertex Selections *(dual purpose)*

| Context | Payload |
|---------|---------|
| Settlement placement | Large list of all valid vertex IDs |
| City upgrade | Small list of existing settlement vertex IDs |

Bot handles Type 30 only during initial placement (`vp ≤ 1`). For regular settlements and city upgrades, the bot uses its own validity logic.

---

### Type 31 — Available Road Placement Edges
Used during initial placement after placing the first / second settlement.

- `payload` — list of edge IDs where a road can be placed

---

### Type 33 — Available Robber Placement Hexes
Sent after rolling a 7 or playing a knight card.

- `payload` — list of **hex IDs** (0–18) where the robber can be placed
- Confirmed as hex IDs, not vertex IDs

---

### Type 43 — Resource Exchange
Fired for player trades, bank trades, and building purchases (player → bank).

| Field | Description |
|-------|-------------|
| `givingPlayer` | Player color giving resources |
| `givingCards` | List of card IDs given |
| `receivingPlayer` | Player color receiving (`0` = bank) |
| `receivingCards` | List of card IDs received |

---

### Type 78 — Unknown
Post-initialization message. Ignored by bot.
```json
{ "isActive": true, "hasUsedDisableRequest": false }
```

---

### Type 91 — Incremental Game State Update
Most common message. `payload.diff` contains only changed fields.

#### `diceState`
> **Not used by bot** — use `turnState` instead.
```json
{ "diceThrown": true }
```

#### `mapState`
```json
{
  "tileCornerStates": {
    "7": { "owner": 1, "buildingType": 1 }
  },
  "tileEdgeStates": {
    "40": { "owner": 2, "type": 1 }
  }
}
```
- `buildingType`: `1` = settlement, `2` = city

#### `playerStates`
```json
{
  "1": {
    "victoryPointsState": { "0": 3 },
    "resourceCards": {
      "cards": [4, 1, 4]
    }
  }
}
```
- `cards` contains the full hand for **our** player; all `0`s for enemies

#### `mechanicDevelopmentCardsState`
```json
{
  "bankDevelopmentCards": { "cards": [10, 10, 10] },
  "players": {
    "2": {
      "developmentCards": { "cards": [10] },
      "developmentCardsUsed": [11],
      "hasUsedDevelopmentCardThisTurn": true
    }
  }
}
```
- `10` = hidden / generic card; real values shown only for our player

#### `mechanicRobberState`
```json
{ "locationTileIndex": 5 }
```

#### `currentState`
```json
{
  "completedTurns": 4,
  "currentTurnPlayerColor": 2,
  "turnState": 1,
  "actionState": 0,
  "startTime": 1781213494472,
  "allocatedTime": 18000
}
```

| Field | Notes |
|-------|-------|
| `turnState` | `1` = need to roll, `2` = rolled (can build / trade / end turn) |
| `actionState` | **Not used by bot** — use `turnState` instead |
| `allocatedTime` | In **milliseconds** (e.g. `18000` = 18 seconds) |

`actionState` values for reference:
| Value | Meaning |
|-------|---------|
| `0` | Need to roll dice |
| `1` | Placing initial settlement |
| `3` | Placing initial road |
| `24` | Another player is placing robber after rolling 7 |

#### `tradeState`
```json
{
  "activeOffers": {
    "pKhp": {
      "id": "pKhp",
      "creator": 2,
      "offeredResources": [2, 2, 1, 1],
      "wantedResources": [4],
      "playerResponses": { "1": 0 },
      "counterOfferInResponseToTradeId": null,
      "playersCreatingCounterOffer": { "1": false, "2": false }
    },
    "WCgg": null
  }
}
```

- **Non-null value** → new or updated active offer
- **`null` value** → offer was closed / cancelled; remove from active offers
- `closedOffers` also appears in some diffs — **ignore it entirely**
- `playerResponses` key **absent** = player has not yet responded
- `playerResponses` values: `0` = accept, `1` = decline (same as Action 50)

---

## WS OUT Actions

All messages carry an incrementing `out_sequence`.

| Action | Name | Payload |
|--------|------|---------|
| **0** | Send Chat Message | `"message string"` |
| **2** | Roll Dice | `true` |
| **3** | Place Robber | hex ID (0–18) |
| **5** | Steal From Player | player color (e.g. `2`) |
| **6** | End Turn | `true` |
| **7** | Confirm Discard | list of card IDs, e.g. `[1, 3, 5, 5]` — full list in one action |
| ~~8~~ | ~~Update Discard Selection~~ | *Not used — Action 7 handles the full discard* |
| **9** | Buy Dev Card | `true` |
| ~~10~~ | ~~Highlight Road Button~~ | *Useless* |
| **11** | Place Initial Road | edge ID |
| **12** | Build Road | edge ID |
| ~~14~~ | ~~Highlight Settlement Button~~ | *Useless* |
| **15** | Place Initial Settlement | vertex ID |
| **16** | Build Settlement | vertex ID |
| **17** | Click Buy City Button | `true` |
| **18** | Confirm City Upgrade | vertex ID of settlement to upgrade |
| **19** | Build City | vertex ID of settlement to upgrade ⚠️ |
| **21** | Second Road (road-building card) | edge ID |
| **47** | Write Trade / Cancel Highlight | `true` |
| **48** | Play Dev Card | `11` = knight, `12` = VP card, `14` = road building |
| **49** | Send Trade Offer | see `action49.json` |
| **50** | Respond to Trade Offer | `{ "id": "<offer_id>", "response": 0–3 }` |
| **51** | Finalize Trade | see `action51.json` |
| ~~53~~ | ~~Highlight Dev Card~~ | *Not needed — action 48 plays the card directly* |
| **54** | Pause Game | `true` |
| **64** | End Game Signal | `true` |
| ~~66~~ | ~~Hover to Place Road/Settlement~~ | *Useless* |
| **67** | Error Message | bug ID |

### Action 48 — Dev Card Play Sequences

**Knight (payload `11`)** — play before rolling dice:
1. Send action 48 `payload: 11`
2. Type 33 arrives → bot places robber (action 3)
3. Type 29 may arrive → bot steals from richest player (action 5)
4. Next type 91 → bot rolls dice

**Road Building (payload `14`)** — play after rolling during turn:
1. Send action 48 `payload: 14`
2. Type 31 arrives → place first road (action 21)
3. Type 31 arrives again → place second road (action 21)
Both free roads use action 21, confirmed via live testing.

**VP Card (payload `12`)** — no action needed:
VP cards are counted automatically in `victoryPointsState`. No action 48 required.

**Monopoly (payload `13`)** — play during turn:
1. Send action 48 `payload: 13`
2. Type 18 arrives → bot responds with action 7 `payload: [chosen_resource]`
3. Bot picks resource that maximises `opponent_total_of_that_resource × need_score`

**Year of Plenty (payload `15`)** — play during turn:
1. Send action 48 `payload: 15`
2. Type 18 arrives with `developmentCardUsed: 15`, `amountOfCardsToSelect: 2` → bot responds with action 7 `payload: [r1, r2]`
3. Bot picks the 2 resources with the highest unmet-build-need score (duplicates allowed if one resource is needed twice)

### Dev Card IDs (in `developmentCards.cards`)
| ID | Card |
|----|------|
| 10 | Hidden (enemy hand) |
| 11 | Knight |
| 12 | VP card |
| 13 | Monopoly |
| 14 | Road building |
| 15 | Year of plenty |

### Action 50 — Respond to Trade
| Response | Meaning |
|----------|---------|
| `0` | Accept |
| `1` | Decline |
| `2` | Creating counter offer |
| `3` | Cancel counter offer |

> ⚠️ **Action 19 (Build City)**: Unverified whether Actions 17 and/or 18 must precede it. Needs testing.
