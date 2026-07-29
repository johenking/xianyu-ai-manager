import React, { useEffect, useState } from 'react';

import { fetchAuthenticatedBlob } from '../../services/request';


const requiresAuthenticatedFetch = (src: string) => (
  src.startsWith('/api/')
  || src.startsWith('/qr-login/')
  || src.startsWith('/face-verification/')
);


const AuthenticatedImage: React.FC<React.ImgHTMLAttributes<HTMLImageElement>> = ({
  src = '',
  ...props
}) => {
  const [resolvedSrc, setResolvedSrc] = useState(
    requiresAuthenticatedFetch(src) ? '' : src,
  );

  useEffect(() => {
    if (!src || !requiresAuthenticatedFetch(src)) {
      setResolvedSrc(src);
      return undefined;
    }

    const controller = new AbortController();
    let objectUrl = '';
    setResolvedSrc('');
    void fetchAuthenticatedBlob(src, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setResolvedSrc(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setResolvedSrc('');
      });

    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [src]);

  if (!resolvedSrc) return null;
  return <img {...props} src={resolvedSrc} />;
};


export default AuthenticatedImage;
