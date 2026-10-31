import { Page, PageHeader } from "@/components/page";
import { Prose } from "@/components/prose";

export const metadata = { title: "Terms" };

export default function Terms() {
  return (
    <Page>
      <PageHeader eyebrow="Legal" title="Terms of use" description="Template terms. Have counsel review before public launch." />
      <Prose>
        <p><strong>Software, not an adviser.</strong> AGENT//98 provides software that reads public blockchain data and prepares unsigned transactions for your review. It does not provide investment, legal or tax advice, and outputs are not recommendations.</p>
        <p><strong>You sign, you decide.</strong> Every transaction is authorized solely by you in your own wallet. The service never holds your keys and cannot move your funds.</p>
        <p><strong>Utility pass.</strong> A pass is a digital access credential. It is not a security, share, or claim on revenue, and it is not marketed as an investment. Purchases are final once confirmed on-chain.</p>
        <p><strong>Fair use.</strong> Usage credits are limited per period as described in the documentation. Abuse, scraping, or attempts to bypass rate limits or the policy engine may result in suspended access.</p>
        <p><strong>No warranty.</strong> The service is provided “as is”. Simulations, quotes and risk scans can be wrong or stale. To the maximum extent permitted by law, liability is limited to the amount you paid for passes in the preceding 12 months.</p>
        <p><strong>Eligibility.</strong> You are responsible for compliance with the laws of your jurisdiction, including sanctions laws.</p>
      </Prose>
    </Page>
  );
}
