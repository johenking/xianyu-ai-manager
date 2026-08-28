import React, { useEffect, useRef, useState } from 'react';

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
  const displayedBlobRef = useRef('');

  useEffect(() => {
    if (!src || !requiresAuthenticatedFetch(src)) {
      setResolvedSrc(src);
      return undefined;
    }

    const controller = new AbortController();
    let createdUrl = '';
    void fetchAuthenticatedBlob(src, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        createdUrl = URL.createObjectURL(blob);
        const previous = displayedBlobRef.current;
        displayedBlobRef.current = createdUrl;
        setResolvedSrc(createdUrl);
        if (previous && previous !== createdUrl) URL.revokeObjectURL(previous);
      })
      .catch(() => {
        // Keep the last good frame visible when a refresh fails.
      });

    return () => {
      controller.abort();
    };
  }, [src]);

  useEffect(() => () => {
    if (displayedBlobRef.current) URL.revokeObjectURL(displayedBlobRef.current);
  }, []);

  if (!resolvedSrc) return null;
  return <img {...props} src={resolvedSrc} />;
};


export default AuthenticatedImage;
