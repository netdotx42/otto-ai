"use client";
import { Page } from "@/components/page";
import { Button } from "@/components/ui/button";

export default function Error({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <Page className="py-32 text-center">
      <div className="mono text-sm text-bad">Something went wrong</div>
      <p className="mt-2 text-sm text-muted">{error.message}</p>
      <Button className="mt-6" variant="outline" onClick={reset}>Try again</Button>
    </Page>
  );
}
