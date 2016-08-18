import sys
from subprocess import Popen, PIPE
import glob
import radix
from collections import defaultdict
import numpy as np
import simhash
import matplotlib.pylab as plt
import hashlib
import cPickle as pickle
from multiprocessing import Pool
import mmh3

def readrib(files):
    
    rtree = radix.Radix() 
    root = rtree.add("0.0.0.0/0")

    p0 = Popen(["bzcat"]+files, stdout=PIPE, bufsize=-1)
    p1 = Popen(["bgpdump", "-m", "-v", "-t", "change", "-"], stdin=p0.stdout, stdout=PIPE, bufsize=-1)

    for line in p1.stdout: 
        res = line.split('|',16)
        zTd, zDt, zS, zOrig, zAS, zPfx, sPath, zPro, zOr, z0, z1, z2, z3, z4, z5 = res
        
        if zPfx == "0.0.0.0/0":
            continue

        if not zOrig in root.data:
            root.data[zOrig] = {"nbPrefix": 0, "asCount": defaultdict(int)}

        node = rtree.add(zPfx)
        node.data[zOrig] = {"path": set(sPath.split(" ")), "asCount": defaultdict(int)}
        root.data[zOrig]["nbPrefix"] += 1
        for asn in node.data[zOrig]["path"]:
            root.data[zOrig]["asCount"][asn] += 1
    
    return rtree


def readupdates(filename, rtree):

    p0 = Popen(["bzcat", filename], stdout=PIPE, bufsize=-1)
    p1 = Popen(["bgpdump", "-m", "-v", "-"], stdin=p0.stdout, stdout=PIPE, bufsize=-1)
    update_tag=""
    
    root = rtree.search_exact("0.0.0.0/0")
    
    for line in p1.stdout:
        line=line.rstrip("\n")
        res = line.split('|',15)
        zOrig = res[3]
        zPfx  = res[5]

        if zPfx == "0.0.0.0/0":
            continue
        
        if not zOrig in root.data:
            root.data[zOrig] = {"nbPrefix": 0, "asCount": defaultdict(int)}

       
        if res[2] == "W":
            node = rtree.search_exact(res[5])
            if not node is None and zOrig in node.data and len(node.data[zOrig]["path"]):
                root.data[zOrig]["nbPrefix"] -= 1
                for asn in node.data[zOrig]["path"]:
                    root.data[zOrig]["asCount"][asn] -= 1
                node.data[zOrig]["path"] = []
        
        else:
            zTd, zDt, zS, zOrig, zAS, zPfx, sPath, zPro, zOr, z0, z1, z2, z3, z4, z5 = res

            if zPfx == "0.0.0.0/0":
                continue

            node = rtree.search_exact(zPfx)
            path_list = sPath.split(' ')

            if node is None or not zOrig in node.data:
                node = rtree.add(zPfx)
                node.data[zOrig] = {"path": set(sPath.split(" ")), "asCount": defaultdict(int)}
                root.data[zOrig]["nbPrefix"] += 1
                for asn in node.data[zOrig]["path"]:
                    root.data[zOrig]["asCount"][asn] += 1

            else:
                for asn in node.data[zOrig]["path"]:
                    root.data[zOrig]["asCount"][asn] -= 1
                node.data[zOrig]["path"] = set(sPath.split(" "))
                for asn in node.data[zOrig]["path"]:
                    root.data[zOrig]["asCount"][asn] += 1

    return rtree


def hashfunc(x):
    return int(hashlib.sha512(x).hexdigest(), 16)

def sketchesSimhash(sketches):

    hashes = {}
    for sketch, asProb in sketches.iteritems():
        hashes[sketch] = simhash.Simhash(asProb, f=64)

    return hashes

def sketchSet():
    return defaultdict(dict)

def sketching(asProb, pool, N=6, M=16):

    seeds = [2**i for i in range(N)]
    sketches = defaultdict(sketchSet) 
    for seed in seeds:
        for asn, prob in asProb.iteritems():
            sketches[seed][mmh3.hash128(asn,seed=seed)%M][asn] = prob

    # compute the simhash for each hash function
    hashes= pool.map(sketchesSimhash, sketches.itervalues())

    return dict(zip(sketches.keys(), hashes)), sketches


def computeSimhash(rtree, pool):

    root = rtree.search_exact("0.0.0.0/0")
    asProb = defaultdict(list)
    nbPrefixList = []
    # For each RIB from our peers
    for peer, count in root.data.iteritems():
        nbPrefix = count["nbPrefix"]
        if nbPrefix <= 400000:
            continue

        asCount = count["asCount"]
        nbPrefixList.append(nbPrefix)

        # asCount = defaultdict(int)

        # nbPrefix = 0
        # for node in rtree:
            # for asn in node.data["path"]:
                # asCount[asn] += 1

            # nbPrefix += 1

        for asn, nbPath in asCount.iteritems():
            asProb[asn].append(nbPath/float(nbPrefix))

    asAggProb = {}
    for asn, problist in asProb.iteritems():
        mu = np.median(problist)
        if asn == "4788" or asn == "200759" or asn == "65021":
            print "%s: %s" % (asn, mu)
        #if mu > 0.00001:
        asAggProb[asn] = mu

    #print asAggProb

    print "\t%s/%s peers" % (len(nbPrefixList), len(root.data))
    #nbp = [v["nbPrefix"] for  v in root.data.values()]
    #print "\t%s" % nbp
    print "\t%s prefixes per peers" % np.mean(nbPrefixList)
    print "\tTotal number of ASN: %s" % len(asProb)
    print "\tNumber of ASN kept for the hash: %s" % len(asAggProb)

    # sketching
    return sketching(asAggProb, pool)
    # return simhash.Simhash(asAggProb, f=512, hashfunc=hashfunc)

def compareSimhash(prevHash, curHash, sketches,  distThresh=3, minVotes=5):
    distance = 0
    votes = defaultdict(int)
    for seed, sketchSet in prevHash.iteritems():
        for m, prevHash in sketchSet.iteritems():
            if prevHash.distance(currHash[seed][m]) > distThresh:
                for asn in sketches[seed][m].keys():
                    votes[asn]+=1

    return [asn for asn, count in votes.iteritems() if count >= minVotes] 

if __name__ == "__main__":
	
    arguments=sys.argv
    if len(arguments) < 3:
        print("usage: %s ribfiles*.bz2 updatefiles*.bz2" % arguments[0])
        sys.exit()
	
    p=Pool(6)
    arguments.pop(0)
		
    # read rib files
    rib_files = glob.glob(arguments[0])
	
    if len(rib_files)==0:
        print("Files not found!")
        sys.exit()

    rib_files.sort()
    rtree = readrib(rib_files)
    arguments.pop(0)

    hashHistory = {"date":[], "hash":[], "distance":[]}
    prevHash = None

    for arg in arguments:
		
        update_files = glob.glob(arg)
	
        if len(update_files)==0:
            sys.exit()
			
        update_files.sort()

        for fi in update_files:
            rtree = readupdates(fi, rtree)
            currHash, sketches = computeSimhash(rtree, p)

            if not prevHash is None:
                anomalousAsn = compareSimhash(prevHash, currHash)
                #TODO put the following outside of the loop 
                filename = fi.rpartition("/")[2]
                date = filename.split(".")
                date = "%s %s" % (date[1],date[2])

                # distance = prevHash.distance(currHash) 

                # hashHistory["date"].append(date)
                # hashHistory["distance"].append(distance)

                print "%s: %s" % (date, anomalousASN)

            prevHash = currHash

    # plt.figure()
    # plt.plot(hashHistory["distance"])
    # plt.tight_layout()
    # plt.savefig("test.eps")
