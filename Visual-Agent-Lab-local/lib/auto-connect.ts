type AutoConnection<T> = {
  connect: (signal: AbortSignal) => Promise<T>;
  onConnected: (result: T) => void;
  onError: (error: unknown) => void;
  onConnecting: (connecting: boolean) => void;
  retryMs?: number;
};

/** One request at a time; late replies and retries are discarded on unmount. */
export function startAutoConnection<T>({connect,onConnected,onError,onConnecting,retryMs=2000}:AutoConnection<T>) {
  const abort=new AbortController();
  let timer:ReturnType<typeof setTimeout>|undefined;
  async function attempt(){
    onConnecting(true);
    let connected=false;
    try{
      const result=await connect(abort.signal);
      if(!abort.signal.aborted){onConnected(result);connected=true;}
    }catch(error){if(!abort.signal.aborted)onError(error);}
    finally{if(!abort.signal.aborted)onConnecting(false);}
    if(!connected&&!abort.signal.aborted)timer=setTimeout(()=>void attempt(),retryMs);
  }
  void attempt();
  return ()=>{abort.abort();clearTimeout(timer);};
}
