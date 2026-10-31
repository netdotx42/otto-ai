import Link from "next/link";
import { Page } from "@/components/page";

export default function NotFound() {
  return (
    <Page className="py-32 text-center">
      <div className="mono text-sm text-muted">404 · route not found</div>
      <Link href="/" className="mt-4 inline-block text-sm underline">Return home</Link>
    </Page>
  );
}
