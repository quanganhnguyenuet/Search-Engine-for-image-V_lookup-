"use client";

import { useEffect, useRef, useState } from "react";

import { imageUrl } from "@/lib/showcase";
import { cn } from "@/lib/utils";

/** Decorative corpus thumbnail. It leaves a plain tile when the API cannot serve it. */
export function PreviewImage({ assetId, className }: { assetId: string; className?: string }) {
  const image = useRef<HTMLImageElement>(null);
  const [failed, setFailed] = useState(false);

  // On prerendered pages the request can fail before React attaches onError.
  useEffect(() => {
    const element = image.current;
    if (element?.complete && element.naturalWidth === 0) setFailed(true);
  }, []);

  return (
    <span className={cn("block aspect-square overflow-hidden rounded-md bg-muted", className)}>
      {!failed && (
        // eslint-disable-next-line @next/next/no-img-element -- served by the API, sizes vary
        <img
          ref={image}
          src={imageUrl(assetId)}
          alt=""
          width={160}
          height={160}
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full bg-white object-cover"
        />
      )}
    </span>
  );
}
