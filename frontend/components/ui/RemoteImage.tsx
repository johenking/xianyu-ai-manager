import React, { useEffect, useState } from 'react';

interface RemoteImageProps {
  src?: string;
  alt: string;
  className?: string;
  fallback: React.ReactNode;
}

const RemoteImage: React.FC<RemoteImageProps> = ({ src, alt, className, fallback }) => {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [src]);

  if (!src || failed) {
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
      onError={() => setFailed(true)}
    />
  );
};

export default RemoteImage;
