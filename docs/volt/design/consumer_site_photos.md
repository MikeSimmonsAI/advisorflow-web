# CoveragePath (Consumer Leads) website photos: provenance

Site: https://voltmarketing.live/volt/check-coverage. Code: `frontend/src/pages/volt/consumerImages.jsx`. Files: `frontend/public/brands/coveragepath/`. Issue: #619.

All three photos are under the **Pexels License** (https://www.pexels.com/license/). Under that license the photos are:

- free for commercial and non-commercial use
- usable without attribution, although we credit the photographers anyway
- modifiable, so cropping and resizing are allowed

The license does not allow:

- showing identifiable people in a bad light or as endorsing a product or service
- selling unaltered copies

The originals were downloaded on Nov 15, 2026 from `images.pexels.com` at 2400 px wide. They were then cropped and resized, and saved as AVIF and WebP. The site serves only these bundled copies and never links to Pexels.

| Site file | Pexels photo | Photographer | Original (2400 px) sha256 |
|---|---|---|---|
| `hero-couple-{480,800}.{avif,webp}`: main photo in the top section, cropped to 4:5 | https://www.pexels.com/photo/happy-couple-embracing-on-sofa-12884986/ | Centre for Ageing Better (UK charity; its photo library depicts later life positively) | `94c1348c8bb6ad2c127f80ad4d2d10faa1a8d89baffefbf3dd91cb1a170e5f94` |
| `family-laptop-{640,1100}.{avif,webp}`: photo strip, left | https://www.pexels.com/photo/smiling-grandma-with-grandchildren-6248452/ | Kampus Production | `acfc5440051c07b0f444139a87f36bbbb5489c0e45f656108d9edf987659088a` |
| `couple-call-{640,1100}.{avif,webp}`: photo strip, right | https://www.pexels.com/photo/elderly-couple-sitting-on-the-sofa-6817697/ | Yan Krukau | `d94c1ac964232c1b65d04e837ede8d0e180a997350c9de12d9c58391459ae2ba` |

## Rules for these photos

- The people shown are not customers. The page never names them, quotes them, or ties them to a result or a review. The footer says so: "Photos: Centre for Ageing Better, Kampus Production and Yan Krukau on Pexels. The people shown are not customers."
- The photos contain no doctors, credentials, insurance cards, government seals, or AI-generated people.
- **Candidates rejected:**
  - A popular Pexels "happy senior couple on a sofa" series by RDNE Stock project, because the same shoot is a cannabis series.
  - Personal Flickr family snapshots found through Openverse, because they show private people in an advertising context.
  - Unsplash+ photos, because they need a paid license.
- **Model releases:** free photo libraries (Pexels, Unsplash) do not supply them. Before a paid campaign uses these images in ads off this website, check with the photographer or swap in licensed stock photos that come with releases.

To replace a photo: put new files in `frontend/public/brands/coveragepath/` at the same widths, update `consumerImages.jsx`, and add a row to the table above.
