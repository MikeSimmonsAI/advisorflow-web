/**
 * WHAT THE POST-SEND DIALOG IS ALLOWED TO CLAIM — one decision, not four.
 *
 * ===========================================================================
 * THE DEFECT THIS EXISTS TO CLOSE
 * ===========================================================================
 *
 * `SendOnboarding.jsx` showed three statements at once, and one of them was
 * false:
 *
 *     Invitation sent                      (heading, keyed on delivery)
 *     Sent — to joshua@…                   (delivery block, keyed on delivery)
 *     Nothing has been sent.               (amber block, keyed on ACCESS PATH)
 *
 * The amber block was written when this module could only mint a link — back
 * then "a setup link was issued" and "nothing was sent" were the same
 * sentence, so keying it on `access_path` was correct. When `deliver` was
 * added, the heading and the delivery block were re-keyed to what actually
 * happened and this one was not. It then fired for every recipient who had no
 * password yet, including one the provider had genuinely accepted an email
 * for. Observed live on Atlantis Light & Power: message_sent_by_platform
 * true, delivery state "sent", and the dialog still saying nothing had been.
 *
 * ===========================================================================
 * THE TWO CLAIMS, SEPARATED
 * ===========================================================================
 *
 * The amber block was making two claims that are not the same claim:
 *
 *   "nothing was sent"     — about DELIVERY. False the moment the provider
 *                            accepts the message.
 *   "this link is shown    — about the LINK. True whenever a one-time setup
 *    once, keep it"          link was minted, whether or not it was mailed,
 *                            because closing the dialog loses it either way.
 *
 * So they are answered separately here, and the dialog renders whichever are
 * true. Nothing in this file renders anything; it decides, and it imports
 * nothing so `tests/frontend/sendOnboardingNotices.test.mjs` can run the whole
 * matrix under plain `node`.
 */

/** Did the platform itself put the message in front of the recipient? */
export function wasDelivered(sent) {
  return sent?.message_sent_by_platform === true
}

/** Was a one-time password-setup link minted, as opposed to reusing a login? */
export function isSetupLink(sent) {
  return sent?.access_path !== 'existing_login'
}

/**
 * Which notices the post-send dialog should show.
 *
 * @param sent the `POST /god/launch/{org}/send-onboarding` response
 */
export function postSendNotices(sent) {
  const delivered = wasDelivered(sent)
  const setupLink = isSetupLink(sent)

  return {
    heading: delivered ? 'Invitation sent' : 'Onboarding ready to send',

    // Their credentials were not touched and no link exists to guard.
    existingLogin: !setupLink,

    // THE MANUAL-SEND WARNING. Only when the platform did not send it —
    // an operator who assumes an email went out waits for a reply that is
    // never coming, and an operator told to send it themselves after the
    // platform already did will send it twice.
    manualSend: setupLink && !delivered,

    // THE LINK IS STILL ONE-TIME EITHER WAY. Shown on its own once the
    // manual-send warning is gone, so a successful send does not silently
    // drop the only statement about the link's shelf life.
    keepLink: setupLink && delivered,

    // What the provider actually said, whenever a send was attempted —
    // including a failure, which is the case an operator most needs to see.
    deliveryReport: Boolean(sent?.delivery && sent.delivery.state !== 'generated'),

    linkLabel: setupLink ? 'One-time onboarding link' : 'Their onboarding address',
  }
}
