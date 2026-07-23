import React, { useState } from 'react';

interface RemoteImageProps {
  src?: string;
  alt: string;
  className?: string;
  fallback: React.ReactNode;
}

const RemoteImage: React.FC<RemoteImageProps> = ({ src, alt, className, fallback }) => {
  const [failedSrc, setFailedSrc] = useState<string | undefined>();

  if (!src || failedSrc === src) {
    return <>{fallback}</>;
  }

  return (
    <img
      src={src}
      alt={alt}
      className={className}
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailedSrc(src)}
    />
  );
};

export default RemoteImage;
