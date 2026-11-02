# P5-3: trust model plan, conditions 6 and 7 (who may be believed, on what anchor, at what time)

Status: **plan**, 2026-11-02. Design only. It adds no code, no policy file, no key, no certificate, no timestamp and no release; it changes no verifier. It chooses no trust anchor, no authority and no time source: those are the owner's, and section 8 lists them. It follows `docs/P5-RELEASE-ORIGIN-ADDENDUM.md` (conditions 6 and 7 of its section 4) and is independent of the digest mapping plan (condition 8).

**The question this plan answers.** A signature that verifies is a fact about keys. Whether a release is *accepted* is a decision about **who** may sign, **what** root of trust the verifier starts from, and **when** the certificates are judged. None of the three can be derived from the signature itself. This plan separates them, measures what the present record and verifier can and cannot say, and answers one date-driven question precisely: **on what conditions can a record signed before 2026-12-31 be verified in 2027?**

## 1. Time-critical facts (measured 2026-11-02)

Every certificate of the present chain ends on the same day:

| Certificate | Role | Not after (UTC) |
|---|---|---|
| CIC Developer CA | issues the developer's certificate | 2026-12-31 13:22:46 |
| CIC Source CA | the countersigning authority (a CA key, `pathlen:0`, used directly to sign) | 2026-12-31 13:22:39 |
| developer certificate | `release.sign` | 2026-12-31 13:22:59 |
| CIC Root CA | self-signed root, `pathlen:3` | 2026-12-31 23:59:59 |

(All four start in March 2026. The developer commitment of the repository runs to 2027-05-25 but rests on the developer certificate above.) **59 days remain.**

**The present verifier rejects every release of this chain from 2027-01-01.** Measured by running the verifier's own signature check on the record of `v0.2.1` with the clock set to three days:

| Verification date | `release.sign` | `cic_countersign` |
|---|---|---|
| 2026-11-02 | OK | OK |
| 2026-12-31 12:00 | OK | OK |
| 2027-01-15 | `Certificate not valid: 2026-03-20 -> 2026-12-31` | `Certificate not valid: 2026-03-20 -> 2026-12-31` |

The check compares the validity window with the time **now**; it has no way to be asked "was this valid when it was signed". The records of `v0.2.0` and `v0.1.5` were measured the same way: both signatures of both records pass on 2026-11-02 and fail on 2027-01-15 with the same message. Nothing is wrong with the signatures; the verifier has no notion of time of signing.

## 2. What the record and the chain can and cannot say (measured)

| # | Fact | Consequence |
|---|---|---|
| 1 | The record has a `timestamp` (`2026-11-02T18:57:58+00:00`). It is a member of the bundle, so `build_hash` and `release.sign` cover it. It is **the signer's own clock**: signed, but not independent evidence of when | usable as a claim, never as proof of time |
| 2 | `cic_countersign.ts` (`2026-11-02T18:58:57Z`) and `workflow` are returned by the service, but the countersignature is made over the digest `build_hash` **only**; `ts` is set afterwards and is **not signed** | anyone can edit `ts` without invalidating anything; it proves nothing |
| 3 | The countersigning service writes an audit log with a timestamp per request | a local record of the operator, not evidence a third party can check |
| 4 | The record carries three certificates (developer, Source CA, Root) but **not the Developer CA**: the developer certificate does not chain to the root from the record's material alone (`unable to get local issuer certificate`); with the Developer CA from the local PKI directory it does | the issuer's chain is not reproducible from the record; the CA certificates have to be preserved by someone |
| 5 | The developer certificate is a leaf (`CA:FALSE`, `Digital Signature`) whose extended key usage is `TLS Web Client Authentication` and `E-mail Protection`. There is **no** release-signing purpose | the certificate does not say that its holder may sign releases; that has to come from a policy outside it |
| 6 | The Source CA certificate is a CA (`pathlen:0`) without an extended key usage, and its key signs the countersignatures directly | the authority is a CA key used as a signing key; what it is *authorized* to attest is again not in the certificate |
| 7 | No certificate carries a CRL distribution point or an authority information access; the verifier has no revocation code; the PKI directory holds an `emergency.crl` of unknown scope | revocation is not checked anywhere today; the plan must say whether that is a decision or a gap |
| 8 | A trust anchor is compared only when the caller passes `--trust-root`; otherwise the record is checked against the root **it carries itself** | without a pinned anchor the chain proves consistency, not origin |
| 9 | The two signatures verify under two distinct certificates and public keys. Separate Vault instances are not visible in the record | role separation is shown by distinct keys only |

## 3. Four questions, kept apart

1. **Anchor.** Which root is believed, pinned where, changed by whom, and how is a change made visible.
2. **Authority.** Which identities may produce `release.sign`, which may produce `cic_countersign`, and what a valid countersignature alone is worth.
3. **Time.** At which moment the validity of each certificate is judged, and what proves that moment.
4. **Rotation and revocation.** How a chain is replaced, what happens to records signed under the old one, and how a compromised key is withdrawn.

A green signature check answers none of them. A producer of receipts may use a release only if all four have a stated answer; until then `authentic-release-origin` stays `not_proved`.

## 4. The 2027 question: a record signed before 2026-12-31

A new certificate does **not** make an old signature valid. The old signature verifies under the old public key and the old certificate, and only the old chain's validity window is relevant to it. A new chain can sign new records, and it can sign a **new statement about** an old record (a renewal, section 6); it cannot reach back.

A record signed under the present chain is acceptable after 2026-12-31 only if all of the following hold. Each is a condition of a policy the verifier would have to implement and the owner to adopt:

* **C1. Independent evidence that both signatures existed before the expiry.** The signer's `timestamp` and the unsigned `ts` do not count (section 2, facts 1 and 2). The evidence has to come from a party the verifier trusts for time (section 5). Without it a verifier can only say "signed under a chain that has expired", and cannot tell a record signed in 2026 from one signed in 2027 by a holder of a stolen old key.
* **C2. The validity of each certificate judged at that proven time**, not at "now" (an explicit as-of mode; the default stays "now").
* **C3. The complete chain at that time.** The Developer CA, the Source CA and the Root, preserved with their fingerprints, because the record does not carry the Developer CA (fact 4).
* **C4. No revocation of a chain element before that time**, against a revocation source the policy names; if there is none, the policy states in words that revocation is not checked (fact 7).
* **C5. The old root pinned as a historical anchor with its window** (2026-03-19 to 2026-12-31), as an entry of the trust store distinct from any new root. Whether a new root vouches for the old one (a succession statement) is a decision (T6).
* **C6. Acceptable algorithms at the time of the check** (ECDSA P-256 with SHA-256 today).

**The one step that cannot be done late.** Evidence that something existed *before* the expiry has to be created *before* the expiry. A timestamp obtained on 2027-01-05 proves "not later than 2027-01-05", which is after the chain ended, and says nothing in favour of the earlier date. So preserving time evidence for `v0.2.1` (and for the older releases, if they matter) is a task with a deadline of **2026-12-31 13:22 UTC**; everything else in this plan can wait for the owner's decisions, this cannot.

## 5. Sources of time evidence

| Option | What it is | Independent of the signer | Checkable offline later | State today |
|---|---|---|---|---|
| E1. Authority-signed time | the countersigning service signs `{build_hash, ts, workflow}` instead of `build_hash` alone | partly: it trusts the Source CA's clock and key, within the CIC trust domain | yes, with the preserved chain | **does not exist**: the signed payload is the constant `build_hash`, in the record schema and the service; a change in both. An old record could get a second, time-bearing attestation only while the Source CA is still valid |
| E2. RFC 3161 time-stamp token | a time-stamping authority signs the digest of the record | yes, a third party | yes, with the TSA's certificate chain retained | not done; needs a choice of authority; only a digest leaves the machine |
| E3. Public transparency log | an inclusion proof of the record digest in an append-only log | yes | yes, with the log's public key | not done; publishes the digest and the signer certificate |
| E4. Hosting-platform evidence | the push and tag events of the repository host, commit dates | no: platform trust, and author dates are editable | no | exists for `v0.2.1` (R pushed 2026-11-02); corroboration only |
| E5. The service's audit log | the operator's own log | no | no | exists; corroboration only |

A recommendation, not a decision: **E2 (or E3) for `v0.2.1` and the older releases now, before the deadline**, because it is the only option that is independent of the signer and does not need a change of the service; **E1 for new records** as the lasting design inside the CIC domain; E4 and E5 kept as corroboration and never as the proof. The choice of an external authority sends a digest out of the machine and is the owner's.

## 6. Rotation, renewal, revocation

* **Rotation.** A new chain has to exist before the old one ends, with an overlap, so that a release can still be made and countersigned on 2026-12-30. The verifier's trust store becomes a **list of anchors**, each with its fingerprint, its window and its status (`current`, `historical`, `withdrawn`), not one pinned file.
* **Renewal of an old record.** A statement by the new chain over the old record's `build_hash` and over the time evidence of C1, made while both chains can be shown. It is a **new** attestation with its own time, not a correction of the old signature, and it is optional if C1 to C6 already hold.
* **Revocation.** Three possible rules: (a) none, declared in the policy; (b) a CRL pinned in the policy with its date; (c) a withdrawal of an anchor or an identity by the policy itself (an entry becomes `withdrawn` with a date; a record whose proven time is earlier stays valid, one that is not provable does not). The compromise case needs (c) at least: today there is no way to say "this key must not be believed after date D".

## 7. Authority and the policy that has to exist

Because the certificates do not say who may release (facts 5 and 6), the permission is a **policy file kept outside the record and outside the signers' control**, versioned and changed only by the owner. It would hold, for every row, an identity (the subject and the fingerprint of the public key) and what it may do:

| Policy row | Content |
|---|---|
| anchors | root fingerprints with window and status (section 6) |
| release signers | identities allowed to produce `release.sign`, with the window in which the permission holds |
| countersigners | identities allowed to produce `cic_countersign` |
| rule | whether a record needs **both** a release signer and a countersigner (the record contract already says an absent countersignature is never a pass), and whether the two must be different identities |
| time | the accepted kinds of time evidence and the as-of rule (C1, C2) |
| revocation | the rule of section 6 |
| algorithms | accepted signature and hash algorithms |

The mapping function of the digest plan takes only a record that this policy has accepted; it does not read the policy and does not decide anything in it.

## 8. Decisions for the owner

| # | Decision | Recommendation |
|---|---|---|
| T1 | The root(s) to pin, and where the pin lives and who changes it | a policy file as in section 7, versioned, owner-changed; the root fingerprint announced out-of-band when it changes |
| T2 | Who may sign releases and who may countersign; whether both are required and must differ | both required, each from an allow-list, distinct identities |
| T3 | The source of time for `v0.2.1` and the older releases, **before 2026-12-31 13:22 UTC** | E2 or E3 now, plus E4 and E5 retained as corroboration |
| T4 | The lasting source of time for new records | E1 (the service signs the time), changing the schema and the service in one unit |
| T5 | Revocation: none, a pinned CRL, or withdrawal by the policy | withdrawal by the policy at least, plus a stated position on the CRL |
| T6 | Whether a new root vouches for the old root (succession), and the rotation date | the owner's PKI decision; the verifier must support several anchors with windows either way |
| T7 | Whether the older releases (`v0.1.5`, `v0.2.0`) are kept verifiable | their records are in the same position; the cost of T3 is a digest each |
| T8 | Where the plan's implementation lives (the verifier is in `cic-primitives`; the policy can live there or with the consumer) | the verifier and the as-of mode in `cic-primitives`, the policy file owned by the owner and read by it |

## 9. Units, all after the decisions

A. preserve the chain and the time evidence for `v0.2.1` (T3), the only unit with a deadline; B. the policy file and its schema (T1, T2, T5); C. a verifier with an as-of mode and a trust store of anchors, tested with a fixed clock before, on and after the expiry, and with negative vectors for each of C1 to C6 (a record with no time evidence, an edited `ts`, an expired chain, a withdrawn identity, a missing intermediate); D. the countersign change for new records (T4); E. rotation (T6). None starts before the owner has decided; unit A may start first on a single decision (T3).

## 10. Not claimed

No key, certificate, timestamp, policy or release was created or changed. No anchor is chosen, nobody is declared authorized, and the present verifier is unchanged. The measurements were taken on 2026-11-02 and are not a gate. A successful verification under any future policy is a statement under that policy, not a statement that a release is genuine in itself. `authentic-release-origin` stays `not_proved`; the production receipt, `cic-primitive-engine#63` and this repository's #138 stay as they are.
