import { Page, PageHeader } from "@/components/page";
import { Prose } from "@/components/prose";

export const metadata = { title: "Risk disclosures" };

export default function Risk() {
  return (
    <Page>
      <PageHeader eyebrow="Legal" title="Risk disclosures" />
      <Prose>
        <ul className="space-y-2">
          <li><strong>Smart-contract risk.</strong> The pass contracts and third-party protocols (Uniswap, Jupiter, token contracts) may contain bugs. Do not rely on unaudited deployments.</li>
          <li><strong>Simulation is not a guarantee.</strong> Chain state can change between simulation and inclusion. Quotes expire; minimum-output protection limits but does not eliminate loss.</li>
          <li><strong>Token risk.</strong> Tokens can be malicious: upgradeable logic, blacklists, transfer taxes, freeze authorities, permanent delegates, honeypots. Scans are heuristic and can miss issues.</li>
          <li><strong>AI limitations.</strong> Language models can misread requests. AGENT//98 never lets the model authorize anything; verify every preview field before signing.</li>
          <li><strong>Market risk.</strong> Digital assets are volatile. You can lose all value swapped or transferred.</li>
          <li><strong>Irreversibility.</strong> Confirmed blockchain transactions cannot be reversed by AGENT//98 or anyone else.</li>
          <li><strong>Pass value.</strong> Passes are access credentials, not investments. There is no supply cap, so do not expect scarcity-driven value.</li>
          <li><strong>Operational risk.</strong> RPC outages, indexer lag or the emergency switch can delay or block features.</li>
        </ul>
      </Prose>
    </Page>
  );
}
