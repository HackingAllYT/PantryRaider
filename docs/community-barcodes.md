# Community barcode fixes

Most barcodes you scan are named by Open Food Facts, a free product database
that anyone can edit. It is very good, but some entries are wrong, half
finished, or missing (a soda listed only as "zero sugar", say). When you fix a
product's name on the Pending page, community barcode fixes let that fix help
every other Pantry Raider kitchen that scans the same barcode.

There are two separate switches, in Settings under Inventory & Storage:

- **Use community barcode fixes** (on by default) names a scanned product from
  the list of fixes other kitchens agreed on, before asking Open Food Facts.
  The list is downloaded once a day and every release ships with the latest
  copy, so it also works when you are offline. It never sends anything about
  you or your pantry.
- **Send barcode fixes** (off by default) shares your own fixes. It needs a
  linked Forager account.

## How you send a fix

Turn on Send barcode fixes, then scan as usual. When an item on the Pending
page has the wrong name, brand, or category, correct it there and add the item
to your pantry. That is the whole process: the fix is sent in the background.
Items whose name you leave alone are not sent, and neither are products you
set up yourself in Grocy, since those carry your own naming rather than a fix
to the public listing.

## What a fix contains

- the barcode (store-printed deli and scale labels are never sent, they mean
  something different in every store);
- the name, brand, and category you chose;
- what the lookup had said, so a reviewer can see what was wrong.

Nothing else about your pantry, your other items, or your device is sent.

## Why it needs a Forager account

A fix is a piece of text that other kitchens will see, so each one is tied to
the Forager account that sent it. Your account is never shown alongside a fix,
but it is what lets Forager weigh fixes from long-standing contributors more
than brand-new ones, and stop an account that sends junk. Deleting your
Forager account deletes every fix you sent.

## How a fix is checked before anyone gets it

A single fix is never published on its own. Forager publishes a barcode only
when all of these hold:

- the fixes for it agree on the same product name (small differences in
  spelling, capitals, and punctuation count as the same name);
- they come from at least two different accounts, or from an account the
  Pantry Raider team trusts;
- together they carry enough weight, and they outweigh any competing answer
  for that barcode by at least two to one.

Each account's fixes carry a weight. A new account, or one whose email address
is not confirmed yet, counts for less. An account whose earlier fixes matched
what the community settled on counts for more, and one whose fixes kept
disagreeing, or were turned down on review, counts for less. So a burst of
new accounts cannot outvote a few established ones.

The Pantry Raider team also reviews contested barcodes, and can approve the
right answer, turn down a bad one, or block an account from sending fixes.

## Which answer wins

When you scan a barcode, the app uses, in order:

1. the product as you set it up in Grocy, if the barcode is already there;
2. a community fix;
3. Open Food Facts;
4. an AI guess, if you have AI turned on.

You can always change the name on the Pending page before adding the item.
